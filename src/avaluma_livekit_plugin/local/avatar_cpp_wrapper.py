"""
C++ Wrapper for AvalumaRuntime - LiveKit Integration

This wrapper provides an async interface around the C++ avaluma_runtime module,
making it compatible with LiveKit's async/await patterns.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

import numpy as np

# A/V Sync Debug Flag - set to True to enable detailed timing logs
AV_SYNC_DEBUG = False

# PERFORMANCE: Single-threaded executor to avoid constant EGL context switching
# This ensures all C++ calls run on the SAME thread, keeping EGL context bound
_render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AvalumaRender")


class AudioChunk:
    """Audio chunk wrapper (compatible with existing LiveKit code)"""

    def __init__(self, byte_data: bytes, num_samples: int, sample_rate: int):
        self.bytes = byte_data
        self.num_samples = num_samples
        self.num_channels = 1
        self.sample_rate = sample_rate

    def __repr__(self):
        return f"<AudioChunk: {self.num_samples} samples @ {self.sample_rate} Hz>"


class Frame:
    """Frame wrapper (compatible with existing LiveKit code)"""

    def __init__(
        self,
        audio_chunk: AudioChunk,
        bgr_image: np.ndarray,
        end_of_speech: bool,
        timestamp_us: int = 0,
        frame_number: int = 0,
        duration_us: int = 40000,
    ):
        self.bgr_image = bgr_image
        self.audio_chunk = audio_chunk
        self.end_of_speech = end_of_speech
        # Synchronization fields from C++ runtime
        self.timestamp_us = timestamp_us  # Presentation timestamp in microseconds
        self.frame_number = frame_number  # Sequential frame counter
        self.duration_us = duration_us  # Frame duration (40ms for 25 FPS)

    def __repr__(self):
        return (
            f"<Frame: image={self.bgr_image.shape}, "
            f"audio={len(self.audio_chunk.bytes)} bytes, "
            f"EOS={self.end_of_speech}, "
            f"ts={self.timestamp_us}us, frame#{self.frame_number}>"
        )


class AvalumaRuntimeSettings:
    """Runtime settings (populated from C++ runtime)"""

    def __init__(self, cpp_runtime):
        self.FPS = cpp_runtime.fps
        self.INPUT_SAMPLE_RATE = cpp_runtime.input_sample_rate
        self.HEIGHT = cpp_runtime.height
        self.WIDTH = cpp_runtime.width

    def __repr__(self):
        return (
            f"<AvalumaRuntimeSettings: {self.WIDTH}x{self.HEIGHT} "
            f"@ {self.FPS} FPS, {self.INPUT_SAMPLE_RATE} Hz>"
        )


class AvalumaRuntime:
    """
    Python wrapper around C++ AvalumaRuntime
    Provides async interface for LiveKit integration
    """

    def __init__(self, **kwargs):
        """
        Initialize AvalumaRuntime

        Args:
            license_key: License key string (required)
            avatar_id: Avatar ID (optional, currently unused)
            asset_path: Path to avatar assets directory (required)
            render_width: Render width in pixels (optional, default 512)
            render_height: Render height in pixels (optional, default 640)
            fps: Frames per second (optional, default 25)
        """
        # Validate required parameters
        if "asset_path" not in kwargs:
            raise ValueError("asset_path is required")

        # Create C++ config
        from . import avaluma_runtime

        config = avaluma_runtime.RuntimeConfig()
        config.asset_path = kwargs["asset_path"]
        config.license_key = kwargs.get("license_key", "")

        # Optional rendering parameters
        if "render_width" in kwargs:
            config.render_width = kwargs["render_width"]
        if "render_height" in kwargs:
            config.render_height = kwargs["render_height"]
        if "fps" in kwargs:
            config.fps = kwargs["fps"]

        # Create C++ runtime
        print(f"Creating AvalumaRuntime: {config}")
        self._cpp_runtime = avaluma_runtime.AvalumaRuntime(config)

        # Initialize
        print("Initializing C++ runtime...")
        if not self._cpp_runtime.initialize():
            raise RuntimeError("Failed to initialize C++ AvalumaRuntime")

        print("✓ AvalumaRuntime initialized successfully")

        # Populate settings
        self.settings = AvalumaRuntimeSettings(self._cpp_runtime)
        print(f"  Settings: {self.settings}")

    async def push_audio(self, byte_data, sample_rate, last_chunk=False):
        """
        Push audio data to C++ runtime

        Args:
            byte_data: Raw PCM audio bytes (int16, mono)
            sample_rate: Sample rate (should be 16000)
            last_chunk: Whether this is the last chunk (triggers flush if True)
        """
        # A/V Sync Debug: Log audio push timing
        if AV_SYNC_DEBUG:
            wall_time = time.perf_counter()
            audio_size = len(byte_data)
            audio_duration_ms = (audio_size / 2) / sample_rate * 1000  # int16 = 2 bytes
            print(
                f"[AV_DEBUG] push_audio: wall={wall_time:.3f}s, "
                f"size={audio_size}b, dur={audio_duration_ms:.1f}ms, sr={sample_rate}"
            )

        # Run blocking C++ call in single-threaded executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            _render_executor,
            self._cpp_runtime.push_audio,
            bytes(byte_data),
            sample_rate,
        )

        if last_chunk:
            await self.flush()

    async def run(self) -> AsyncGenerator[Frame, None]:
        """
        Async generator yielding frames

        This is the main entry point for LiveKit integration.
        It continuously generates frames from pushed audio.

        Yields:
            Frame: Combined audio + video frame

        Example:
            async for frame in runtime.run():
                # Send frame.bgr_image to video track
                # Send frame.audio_chunk.bytes to audio track
                if frame.end_of_speech:
                    break
        """
        consecutive_none_count = 0
        max_none_retries = 300  # 30 seconds @ 100ms each (wait for TTS to start)

        while True:
            # Get next frame from C++ (blocking call in single-threaded executor)
            try:
                loop = asyncio.get_event_loop()
                cpp_frame = await loop.run_in_executor(
                    _render_executor,
                    self._cpp_runtime.get_next_frame,
                    40,  # 40ms timeout (matches frame duration @ 25 FPS)
                )
            except Exception as e:
                print(f"Error getting frame: {e}")
                break

            if cpp_frame is None:
                # Could be:
                # 1. Waiting for first audio (C++ returns nullptr non-blocking)
                # 2. Timeout (no audio in buffer)
                # 3. Actually stopped
                consecutive_none_count += 1

                if consecutive_none_count >= max_none_retries:
                    print(f"Timeout: No frames after {max_none_retries} retries (30s)")
                    break  # Really stopped or timeout

                # Wait for audio or next retry (don't spam C++ with requests)
                await asyncio.sleep(0.039)  # 50ms between retries
                continue  # Try again

            # Reset none counter - we got a frame!
            consecutive_none_count = 0

            # Wrap C++ frame in Python objects
            audio_chunk = AudioChunk(
                cpp_frame.audio_chunk.bytes,
                cpp_frame.audio_chunk.num_samples,
                cpp_frame.audio_chunk.sample_rate,
            )

            frame = Frame(
                audio_chunk,
                cpp_frame.bgr_image,  # Already np.ndarray via PyBind11
                cpp_frame.end_of_speech,
                timestamp_us=cpp_frame.timestamp_us,
                frame_number=cpp_frame.frame_number,
                duration_us=cpp_frame.duration_us,
            )

            # A/V Sync Debug: Log frame output timing
            if AV_SYNC_DEBUG:
                frame_wall_time = time.perf_counter()
                print(
                    f"[AV_DEBUG] get_frame: wall={frame_wall_time:.3f}s, "
                    f"ts={frame.timestamp_us}us ({frame.timestamp_us / 1e6:.3f}s), "
                    f"frame#{frame.frame_number}, eos={frame.end_of_speech}"
                )
            # Legacy SYNC_DEBUG: Log every 1000th frame
            elif frame.frame_number % 1000 == 0:
                print(
                    f"SYNC_DEBUG: Python yielding frame #{frame.frame_number}, "
                    f"timestamp={frame.timestamp_us}us ({frame.timestamp_us / 1e6:.3f}s), "
                    f"end_of_speech={frame.end_of_speech}"
                )

            yield frame

    async def flush(self):
        """
        Signal end of speech
        The next generated frame will have end_of_speech=True
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_render_executor, self._cpp_runtime.flush)

    async def stop(self):
        """Stop frame generation"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_render_executor, self._cpp_runtime.stop)

    def interrupt(self):
        """
        Clear audio buffers and reset to neutral pose (synchronous)
        This is typically called when the user interrupts the avatar
        """
        self._cpp_runtime.interrupt()

    def cleanup(self):
        """
        Cleanup resources
        Called automatically by C++ destructor, but can be called explicitly
        """
        pass

    def __repr__(self):
        return f"<AvalumaRuntime: {self._cpp_runtime}>"


# Example usage
if __name__ == "__main__":
    import sys

    async def test_basic_flow():
        """Test basic audio → frame flow"""

        # Create runtime
        runtime = AvalumaRuntime(
            license_key="test_key",
        )

        print(f"\nRuntime created: {runtime.settings}")

        # Generate test audio (1 second @ 16kHz sine wave)
        sample_rate = 16000
        duration = 1.0
        num_samples = int(sample_rate * duration)

        # Simple sine wave (A4 note = 440 Hz)
        freq = 440.0
        t = np.linspace(0, duration, num_samples)
        audio = (np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
        audio_bytes = audio.tobytes()

        # Push audio
        print("\nPushing audio...")
        await runtime.push_audio(audio_bytes, sample_rate, last_chunk=False)

        # Generate frames
        print("\nGenerating frames...")
        frame_count = 0

        async for frame in runtime.run():
            frame_count += 1
            print(f"Frame {frame_count}: {frame}")

            if frame_count >= 25:  # 1 second worth of frames
                break

        await runtime.stop()
        print(f"\n✓ Generated {frame_count} frames successfully")

    # Run test
    try:
        asyncio.run(test_basic_flow())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
