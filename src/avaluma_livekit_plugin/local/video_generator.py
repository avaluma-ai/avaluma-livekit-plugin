import logging
from collections.abc import AsyncGenerator, AsyncIterator

import numpy as np
from livekit import rtc
from livekit.agents import utils
from livekit.agents.voice.avatar import (
    AudioSegmentEnd,
    VideoGenerator,
)

logger = logging.getLogger(__name__)
from .avatar_cpp_wrapper import AvalumaRuntime


class AvalumaVideoGenerator(VideoGenerator):
    def __init__(self, runtime: AvalumaRuntime):
        self._runtime = runtime
        self._audio_resampler = rtc.AudioResampler(
            input_rate=48000, output_rate=16000, quality=rtc.AudioResamplerQuality.QUICK
        )

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

        if frame.sample_rate == 48000:
            print("Resampling audio from 48kHz to 16kHz")
            for resampled_frame in self._audio_resampler.push(frame):
                await self._runtime.push_audio(
                    bytes(resampled_frame.data),
                    resampled_frame.sample_rate,
                    last_chunk=False,
                )
            self._audio_resampler.flush()
        else:
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

        async for frame in self._runtime.run():
            # Convert timestamp from microseconds to seconds (for AVSynchronizer)
            timestamp_s = frame.timestamp_us / 1_000_000.0

            if frame.bgr_image is not None:
                video_frame = create_video_frame(frame.bgr_image, frame.timestamp_us)
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
                yield audio_frame

            if frame.end_of_speech:
                yield AudioSegmentEnd()

    async def stop(self) -> None:
        await self._runtime.stop()
