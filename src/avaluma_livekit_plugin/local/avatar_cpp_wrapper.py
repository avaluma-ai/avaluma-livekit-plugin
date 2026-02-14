"""
C++ Wrapper for AvalumaRuntime - LiveKit Integration

Architecture:
- AvalumaRuntime (singleton) - ONE shared C++ runtime
- AvatarSession (per-avatar) - session for a specific avatar

The C++ runtime uses a multi-session architecture:
1. AvalumaRuntime() - singleton, creates ONE C++ runtime
2. runtime.create_session(asset_path, ...) - returns AvatarSession
3. session.push_audio(...) - push audio for animation
4. session.run() - async generator yielding frames
5. session.destroy() - cleanup

Usage:
    # Get singleton runtime
    runtime = AvalumaRuntime()

    # Create sessions for different avatars
    session1 = runtime.create_session("/path/to/avatar1.hvia")
    session2 = runtime.create_session("/path/to/avatar2.hvia")

    # Use sessions independently
    await session1.push_audio(audio_bytes, 16000)
    async for frame in session1.run():
        ...
"""

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, Optional

import numpy as np

from ..log import logger

# A/V Sync Debug Flag - set to True to enable detailed timing logs
AV_SYNC_DEBUG = True

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

    def __bool__(self):
        return self.bytes is not None and len(self.bytes) > 0


class Frame:
    """Frame wrapper (compatible with existing LiveKit code)"""

    def __init__(
        self,
        audio_chunk: Optional[AudioChunk],
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
        audio_info = f"{len(self.audio_chunk.bytes)} bytes" if self.audio_chunk else "no audio"
        return (
            f"<Frame: image={self.bgr_image.shape}, "
            f"audio={audio_info}, "
            f"EOS={self.end_of_speech}, "
            f"ts={self.timestamp_us}us, frame#{self.frame_number}>"
        )


class AvalumaRuntimeSettings:
    """Runtime settings (passed as constructor params in new API)"""

    def __init__(self, fps: int = 25, sample_rate: int = 48000, width: int = 512, height: int = 640):
        self.FPS = fps
        self.INPUT_SAMPLE_RATE = sample_rate
        self.HEIGHT = height
        self.WIDTH = width

    def __repr__(self):
        return (
            f"<AvalumaRuntimeSettings: {self.WIDTH}x{self.HEIGHT} "
            f"@ {self.FPS} FPS, {self.INPUT_SAMPLE_RATE} Hz>"
        )


class AvalumaRuntime:
    """
    Singleton wrapper around the C++ AvalumaRuntime.

    There is only ONE runtime, which manages multiple avatar sessions.
    Use create_session() to create sessions for different avatars.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # Double-check locking
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        from . import avaluma_runtime

        module_dir = os.path.dirname(avaluma_runtime.__file__)
        logger.info(f"AvalumaRuntime: C++ module at {module_dir}")
        logger.info("AvalumaRuntime: Creating singleton (RAII)")

        self._cpp_runtime = avaluma_runtime.AvalumaRuntime()
        self._initialized = True

        logger.info("AvalumaRuntime: Singleton initialized")

    def create_session(
        self,
        asset_path: str,
        width: int = 512,
        height: int = 640,
        fps: int = 25,
        sample_rate: int = 48000,
    ) -> "AvatarSession":
        """
        Create a new session for an avatar.

        Args:
            asset_path: Path to avatar asset directory (e.g., '/path/to/kadda.hvia')
            width: Render width in pixels (default: 512)
            height: Render height in pixels (default: 640)
            fps: Frames per second (default: 25)
            sample_rate: Audio sample rate in Hz (default: 48000)

        Returns:
            AvatarSession object for the created session
        """
        logger.info(f"Creating session for: {asset_path}")
        session_id = self._cpp_runtime.create_session(
            asset_path=asset_path,
            width=width,
            height=height,
            fps=fps,
            sample_rate=sample_rate,
        )
        logger.info(f"Session created: {session_id}")
        return AvatarSession(self._cpp_runtime, session_id, width, height, fps, sample_rate)

    def get_session_count(self) -> int:
        """Get number of active sessions."""
        return self._cpp_runtime.get_session_count()

    def get_session_ids(self) -> list:
        """Get all active session IDs."""
        return self._cpp_runtime.get_session_ids()

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists."""
        return self._cpp_runtime.has_session(session_id)

    def __repr__(self):
        return f"<AvalumaRuntime: {self.get_session_count()} active sessions>"


class AvatarSession:
    """
    Represents a single avatar session on the shared runtime.

    Each session is tied to ONE avatar (.hvia file).
    Multiple sessions can exist on the same runtime.
    """

    def __init__(
        self,
        cpp_runtime,
        session_id: str,
        width: int,
        height: int,
        fps: int,
        sample_rate: int,
    ):
        self._cpp_runtime = cpp_runtime
        self._session_id = session_id
        self.settings = AvalumaRuntimeSettings(
            fps=fps,
            sample_rate=sample_rate,
            width=width,
            height=height,
        )
        logger.info(f"AvatarSession created: {session_id}, settings: {self.settings}")

    @property
    def session_id(self) -> str:
        """Get the session ID."""
        return self._session_id

    async def push_audio(self, byte_data, sample_rate, last_chunk=False):
        """
        Push audio data to C++ runtime

        Args:
            byte_data: Raw PCM audio bytes (int16, mono)
            sample_rate: Sample rate (should match session sample rate)
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
            self._session_id,
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
            async for frame in session.run():
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
                    self._session_id,
                    40,  # 40ms timeout (matches frame duration @ 25 FPS)
                )
            except Exception as e:
                logger.error(f"Error getting frame: {e}")
                break

            if cpp_frame is None:
                # Could be:
                # 1. Waiting for first audio (C++ returns nullptr non-blocking)
                # 2. Timeout (no audio in buffer)
                # 3. Actually stopped
                consecutive_none_count += 1

                if consecutive_none_count >= max_none_retries:
                    logger.warning(f"Timeout: No frames after {max_none_retries} retries (30s)")
                    break  # Really stopped or timeout

                # Wait for audio or next retry (don't spam C++ with requests)
                await asyncio.sleep(0.039)  # ~40ms between retries
                continue  # Try again

            # Reset none counter - we got a frame!
            consecutive_none_count = 0

            # Wrap C++ audio chunk in Python object (may be empty)
            cpp_audio = cpp_frame.audio_chunk
            if cpp_audio and cpp_audio.bytes:
                audio_chunk = AudioChunk(
                    cpp_audio.bytes,
                    cpp_audio.num_samples,
                    cpp_audio.sample_rate,
                )
            else:
                audio_chunk = None

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
                audio_info = f"{audio_chunk.num_samples} samples" if audio_chunk else "no audio"
                print(
                    f"[AV_DEBUG] get_frame: wall={frame_wall_time:.3f}s, "
                    f"ts={frame.timestamp_us}us ({frame.timestamp_us / 1e6:.3f}s), "
                    f"frame#{frame.frame_number}, {audio_info}, eos={frame.end_of_speech}"
                )
            # Legacy SYNC_DEBUG: Log every 1000th frame
            elif frame.frame_number % 1000 == 0:
                logger.debug(
                    f"Python yielding frame #{frame.frame_number}, "
                    f"timestamp={frame.timestamp_us}us ({frame.timestamp_us / 1e6:.3f}s), "
                    f"end_of_speech={frame.end_of_speech}"
                )

            yield frame

    async def flush(self):
        """
        Signal end of speech.
        The next generated frame will have end_of_speech=True.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            _render_executor,
            self._cpp_runtime.flush,
            self._session_id,
        )

    async def stop(self):
        """Stop frame generation by destroying the session."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            _render_executor,
            self._cpp_runtime.destroy_session,
            self._session_id,
        )

    def interrupt(self):
        """
        Clear audio buffers and reset to neutral pose (synchronous).
        This is typically called when the user interrupts the avatar.
        """
        self._cpp_runtime.interrupt(self._session_id)

    def destroy(self):
        """Destroy this session and release its resources."""
        if self._cpp_runtime.has_session(self._session_id):
            self._cpp_runtime.destroy_session(self._session_id)
            logger.info(f"AvatarSession destroyed: {self._session_id}")

    def cleanup(self):
        """Alias for destroy() for backwards compatibility."""
        self.destroy()

    def __repr__(self):
        return f"<AvatarSession: {self._session_id}>"


# Backwards compatibility: LocalAvatarSession alias
LocalAvatarSession = AvatarSession


# Example usage
if __name__ == "__main__":
    import sys

    async def test_basic_flow():
        """Test basic audio -> frame flow"""

        # Get singleton runtime
        runtime = AvalumaRuntime()
        print(f"Runtime: {runtime}")

        # Create session for avatar
        session = runtime.create_session(
            asset_path="/path/to/avatar.hvia",
        )
        print(f"\nSession created: {session}")
        print(f"Settings: {session.settings}")

        # Generate test audio (1 second @ 48kHz sine wave)
        sample_rate = 48000
        duration = 1.0
        num_samples = int(sample_rate * duration)

        # Simple sine wave (A4 note = 440 Hz)
        freq = 440.0
        t = np.linspace(0, duration, num_samples)
        audio = (np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
        audio_bytes = audio.tobytes()

        # Push audio
        print("\nPushing audio...")
        await session.push_audio(audio_bytes, sample_rate, last_chunk=False)

        # Generate frames
        print("\nGenerating frames...")
        frame_count = 0

        async for frame in session.run():
            frame_count += 1
            print(f"Frame {frame_count}: {frame}")

            if frame_count >= 25:  # 1 second worth of frames
                break

        session.destroy()
        print(f"\n Ã Generated {frame_count} frames successfully")
        print(f"Runtime session count: {runtime.get_session_count()}")

    # Run test
    try:
        asyncio.run(test_basic_flow())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
