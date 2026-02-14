from __future__ import annotations, print_function

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator

from aiohttp.client_exceptions import SSLContext
from livekit.agents.tts.fallback_adapter import AvailabilityChangedEvent
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF
from livekit.agents.utils import log_exceptions
from livekit.agents.voice.avatar import (
    AudioSegmentEnd,
    AvatarOptions,
    AvatarRunner,
)

from livekit import rtc

logger = logging.getLogger(__name__)

# Import debug flag from cpp wrapper
try:
    from .avatar_cpp_wrapper import AV_SYNC_DEBUG
except ImportError:
    AV_SYNC_DEBUG = True


class AvalumaAvatarRunner(AvatarRunner):
    """
    Extended AvatarRunner that uses timestamps from frame.userdata
    for better A/V synchronization
    """

    def __init__(
        self,
        room: rtc.Room,
        *,
        audio_recv,
        video_gen,
        options: AvatarOptions,
        _queue_size_ms: int = 100,
        _lazy_publish: bool = True,
    ) -> None:
        # Parent init (creates AVSynchronizer with defaults)
        super().__init__(
            room,
            audio_recv=audio_recv,
            video_gen=video_gen,
            options=options,
            _queue_size_ms=_queue_size_ms,
            _lazy_publish=_lazy_publish,
        )

        # Recreate AVSynchronizer with doubled values for better A/V sync
        self._av_sync = rtc.AVSynchronizer(
            audio_source=self._audio_source,
            video_source=self._video_source,
            video_fps=options.video_fps,
            video_queue_size_ms=1000,  # doubled from 100
            _max_delay_tolerance_ms=2000,  # doubled from 300
        )

    @log_exceptions(logger=logger)
    async def _forward_video(self) -> None:
        """Forward video to the room through the AV synchronizer with timestamps"""

        async for frame in self._video_gen:
            if isinstance(frame, AudioSegmentEnd):
                # notify the agent that the audio has finished playing
                if self._audio_playing:
                    notify_task = self._audio_recv.notify_playback_finished(
                        playback_position=self._playback_position,
                        interrupted=False,
                    )
                    self._audio_playing = False
                    self._playback_position = 0.0
                    if asyncio.iscoroutine(notify_task):
                        # avoid blocking the video forwarding
                        task = asyncio.create_task(notify_task)
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                continue

            if not self._video_publication:
                await self._publish_track()

            # Extract timestamp from frame attribute (if available)
            timestamp_s = None
            if hasattr(frame, "timestamp_s"):
                timestamp_s = frame.timestamp_s

            # A/V Sync Debug: Log what goes to AVSynchronizer
            if AV_SYNC_DEBUG:
                sync_time = time.perf_counter()
                frame_type = "V" if isinstance(frame, rtc.VideoFrame) else "A"
                ts_str = f"{timestamp_s:.3f}s" if timestamp_s else "None"
                print(
                    f"[AV_DEBUG] av_sync.push: wall={sync_time:.3f}s, "
                    f"type={frame_type}, ts={ts_str}"
                )

            # Push frame with timestamp to AVSynchronizer
            await self._av_sync.push(frame, timestamp=timestamp_s)

            if isinstance(frame, rtc.AudioFrame):
                self._playback_position += frame.duration
