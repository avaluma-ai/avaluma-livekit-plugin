import hashlib
import logging
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator

import numpy as np
from livekit import rtc
from livekit.agents import utils
from livekit.agents.voice.avatar import (
    AudioSegmentEnd,
    VideoGenerator,
)

from .avatar_cpp_wrapper import AV_SYNC_DEBUG, AvatarSession

logger = logging.getLogger(__name__)

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
    def __init__(self, session: AvatarSession):
        self._session = session
        self._first_video_timestamp_us: int | None = None  # For timestamp normalization

    @property
    def video_resolution(self) -> tuple[int, int]:
        return self._session.settings.WIDTH, self._session.settings.HEIGHT

    @property
    def video_fps(self) -> int:
        return self._session.settings.FPS  # type: ignore

    @property
    def audio_sample_rate(self) -> int:
        return self._session.settings.INPUT_SAMPLE_RATE  # type: ignore

    @utils.log_exceptions(logger=logger)
    async def push_audio(self, frame: rtc.AudioFrame | AudioSegmentEnd) -> None:
        if isinstance(frame, AudioSegmentEnd):
            await self._session.flush()
            return
        await self._session.push_audio(
            bytes(frame.data), frame.sample_rate, last_chunk=False
        )

    def clear_buffer(self) -> None:
        self._session.interrupt()

    def __aiter__(
        self,
    ) -> AsyncIterator[tuple[rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd, float | None] | AudioSegmentEnd]:
        return self._stream_impl()

    async def _stream_impl(
        self,
    ) -> AsyncGenerator[tuple[rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd, float | None] | AudioSegmentEnd, None]:
        def create_video_frame(image: np.ndarray, timestamp_us: int) -> rtc.VideoFrame:
            image = image[:, :, [2, 1, 0]]  # BGR to RGB

            # NEW: Compute hash of image data
            image_bytes = image.tobytes()
            image_hash = hashlib.md5(image_bytes[:10000]).hexdigest()[:8]  # Sample first 10KB

            if AV_SYNC_DEBUG:
                print(f"[FRAME-DEBUG-PY] create_video_frame: hash={image_hash}, shape={image.shape}")

            video_frame = rtc.VideoFrame(
                width=image.shape[1],
                height=image.shape[0],
                type=rtc.VideoBufferType.RGB24,
                data=image_bytes,
            )
            return video_frame

        # A/V Sync Debug: Buffers for frame offset
        # Positive offset = delay video (audio first)
        # Negative offset = delay audio (video first)
        video_buffer: deque[tuple[rtc.VideoFrame, float]] = deque()
        audio_buffer: deque[tuple[rtc.AudioFrame, float]] = deque()
        offset = AV_SYNC_OFFSET

        if offset != 0:
            logger.info(f"A/V Sync Debug: Using frame offset {offset} ({offset * 40}ms)")

        async for frame in self._session.run():
            # Normalize timestamps: first video frame starts at 0.0
            if frame.bgr_image is not None and self._first_video_timestamp_us is None:
                self._first_video_timestamp_us = frame.timestamp_us
                if AV_SYNC_DEBUG:
                    print(f"[AV_DEBUG] timestamp_normalization: offset={self._first_video_timestamp_us}us ({self._first_video_timestamp_us / 1_000_000.0:.3f}s)")

            # Convert timestamp from microseconds to seconds (for AVSynchronizer)
            # Use normalized timestamps (relative to first video frame)
            normalized_timestamp_us = frame.timestamp_us
            if self._first_video_timestamp_us is not None:
                normalized_timestamp_us = frame.timestamp_us - self._first_video_timestamp_us
            timestamp_s = normalized_timestamp_us / 1_000_000.0

            if frame.bgr_image is not None:
                video_frame = create_video_frame(frame.bgr_image, normalized_timestamp_us)

                if offset > 0:
                    # Positive offset: Buffer video, yield after delay
                    video_buffer.append((video_frame, timestamp_s))
                    if len(video_buffer) > offset:
                        delayed_video, delayed_ts = video_buffer.popleft()
                        if AV_SYNC_DEBUG:
                            emit_time = time.perf_counter()
                            print(
                                f"[AV_DEBUG] emit_video (delayed +{offset}): wall={emit_time:.3f}s"
                            )
                        yield (delayed_video, delayed_ts)
                else:
                    # Zero or negative offset: Yield video immediately
                    if AV_SYNC_DEBUG:
                        emit_time = time.perf_counter()
                        print(
                            f"[AV_DEBUG] emit_video: wall={emit_time:.3f}s, "
                            f"ts={timestamp_s:.3f}s, frame#{frame.frame_number}"
                        )
                    yield (video_frame, timestamp_s)

            audio_chunk = frame.audio_chunk
            if audio_chunk is not None:
                audio_frame = rtc.AudioFrame(
                    data=audio_chunk.bytes,
                    sample_rate=audio_chunk.sample_rate,
                    num_channels=1,
                    samples_per_channel=audio_chunk.num_samples,
                )

                if offset < 0:
                    # Negative offset: Buffer audio, yield after delay
                    audio_buffer.append((audio_frame, timestamp_s))
                    if len(audio_buffer) > abs(offset):
                        delayed_audio, delayed_ts = audio_buffer.popleft()
                        if AV_SYNC_DEBUG:
                            emit_time = time.perf_counter()
                            print(
                                f"[AV_DEBUG] emit_audio (delayed {offset}): wall={emit_time:.3f}s"
                            )
                        yield (delayed_audio, delayed_ts)
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
                    yield (audio_frame, timestamp_s)

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
        await self._session.stop()
