import logging
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator

import numpy as np
from livekit.agents import utils
from livekit.agents.voice.avatar import (
    AudioSegmentEnd,
    VideoGenerator,
)

from livekit import rtc

logger = logging.getLogger(__name__)
from .avatar_cpp_wrapper import AV_SYNC_DEBUG, AvalumaRuntime

# =============================================================================
# A/V Sync Debug: Frame offset for testing synchronization
# =============================================================================
# Positive = Video verzögert (Audio spielt zuerst)
# Negative = Audio verzögert (Video spielt zuerst)
# 1 frame = 40ms @ 25 FPS
#
# Examples:
#   AV_SYNC_OFFSET = +5   # Audio 200ms voraus
#   AV_SYNC_OFFSET = -5   # Video 200ms voraus
#   AV_SYNC_OFFSET = 0    # Keine Änderung (default)
# =============================================================================
AV_SYNC_OFFSET = 0


class AvalumaVideoGenerator(VideoGenerator):
    def __init__(self, runtime: AvalumaRuntime):
        self._runtime = runtime

    @property
    def video_resolution(self) -> tuple[int, int]:
        return self._runtime.settings.WIDTH, self._runtime.settings.HEIGHT

    @property
    def video_fps(self) -> int:
        return self._runtime.settings.FPS  # type: ignore

    @property
    def audio_sample_rate(self) -> int:
        return self._runtime.settings.INPUT_SAMPLE_RATE  # type: ignore

    @utils.log_exceptions(logger=logger)
    async def push_audio(self, frame: rtc.AudioFrame | AudioSegmentEnd) -> None:
        if isinstance(frame, AudioSegmentEnd):
            await self._runtime.flush()
            return
        await self._runtime.push_audio(
            bytes(frame.data), frame.sample_rate, last_chunk=False
        )

    def clear_buffer(self) -> None:
        self._runtime.interrupt()

    def __aiter__(
        self,
    ) -> AsyncIterator[rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd]:
        return self._stream_impl()

    async def _stream_impl(
        self,
    ) -> AsyncGenerator[rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd, None]:
        def create_video_frame(image: np.ndarray, timestamp_us: int) -> rtc.VideoFrame:
            image = image[:, :, [2, 1, 0]]
            video_frame = rtc.VideoFrame(
                width=image.shape[1],
                height=image.shape[0],
                type=rtc.VideoBufferType.RGB24,
                data=image.tobytes(),
            )
            # Store timestamp as dynamic attribute (for AVSynchronizer)
            video_frame.timestamp_s = timestamp_us / 1_000_000.0
            return video_frame

        # A/V Sync Debug: Buffers for frame offset
        # Positive offset = delay video (audio first)
        # Negative offset = delay audio (video first)
        video_buffer: deque[rtc.VideoFrame] = deque()
        audio_buffer: deque[rtc.AudioFrame] = deque()
        offset = AV_SYNC_OFFSET

        if offset != 0:
            logger.info(f"A/V Sync Debug: Using frame offset {offset} ({offset * 40}ms)")

        async for frame in self._runtime.run():
            # Convert timestamp from microseconds to seconds (for AVSynchronizer)
            timestamp_s = frame.timestamp_us / 1_000_000.0

            if frame.bgr_image is not None:
                video_frame = create_video_frame(frame.bgr_image, frame.timestamp_us)

                if offset > 0:
                    # Positive offset: Buffer video, yield after delay
                    video_buffer.append(video_frame)
                    if len(video_buffer) > offset:
                        delayed_video = video_buffer.popleft()
                        if AV_SYNC_DEBUG:
                            emit_time = time.perf_counter()
                            print(
                                f"[AV_DEBUG] emit_video (delayed +{offset}): wall={emit_time:.3f}s"
                            )
                        yield delayed_video
                else:
                    # Zero or negative offset: Yield video immediately
                    if AV_SYNC_DEBUG:
                        emit_time = time.perf_counter()
                        print(
                            f"[AV_DEBUG] emit_video: wall={emit_time:.3f}s, "
                            f"ts={timestamp_s:.3f}s, frame#{frame.frame_number}"
                        )
                    yield video_frame

            audio_chunk = frame.audio_chunk
            if audio_chunk is not None:
                audio_frame = rtc.AudioFrame(
                    data=audio_chunk.bytes,
                    sample_rate=audio_chunk.sample_rate,
                    num_channels=1,
                    samples_per_channel=audio_chunk.num_samples,
                )
                # Store timestamp as dynamic attribute (for AVSynchronizer)
                audio_frame.timestamp_s = timestamp_s

                if offset < 0:
                    # Negative offset: Buffer audio, yield after delay
                    audio_buffer.append(audio_frame)
                    if len(audio_buffer) > abs(offset):
                        delayed_audio = audio_buffer.popleft()
                        if AV_SYNC_DEBUG:
                            emit_time = time.perf_counter()
                            print(
                                f"[AV_DEBUG] emit_audio (delayed {offset}): wall={emit_time:.3f}s"
                            )
                        yield delayed_audio
                else:
                    # Zero or positive offset: Yield audio immediately
                    if AV_SYNC_DEBUG:
                        emit_time = time.perf_counter()
                        audio_dur_ms = (
                            audio_chunk.num_samples / audio_chunk.sample_rate * 1000
                        )
                        print(
                            f"[AV_DEBUG] emit_audio: wall={emit_time:.3f}s, "
                            f"ts={timestamp_s:.3f}s, dur={audio_dur_ms:.1f}ms"
                        )
                    yield audio_frame

        # FIX: Only yield AudioSegmentEnd ONCE after the C++ loop ends (returns None)
        # Previously this was inside the loop, causing multiple EOS markers
        # because C++ sets end_of_speech=True on EVERY frame after flush()

        # Flush remaining buffered frames
        while video_buffer:
            if AV_SYNC_DEBUG:
                print(f"[AV_DEBUG] flush_video: {len(video_buffer)} remaining")
            yield video_buffer.popleft()
        while audio_buffer:
            if AV_SYNC_DEBUG:
                print(f"[AV_DEBUG] flush_audio: {len(audio_buffer)} remaining")
            yield audio_buffer.popleft()

        if AV_SYNC_DEBUG:
            print(f"[AV_DEBUG] emit_eos: wall={time.perf_counter():.3f}s")
        yield AudioSegmentEnd()

    async def stop(self) -> None:
        await self._runtime.stop()
