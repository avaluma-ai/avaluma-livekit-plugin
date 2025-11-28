from __future__ import annotations, print_function

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator

from aiohttp.client_exceptions import SSLContext
from livekit.agents.tts.fallback_adapter import AvailabilityChangedEvent
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF
from livekit.agents.utils import log_exceptions
from livekit.agents.voice.avatar import (
    AudioSegmentEnd,
    AvatarRunner,
)

from livekit import rtc

logger = logging.getLogger(__name__)


class AvalumaAvatarRunner(AvatarRunner):
    """
    Extended AvatarRunner that uses timestamps from frame.userdata
    for better A/V synchronization
    """

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

            # Push frame with timestamp to AVSynchronizer
            await self._av_sync.push(frame, timestamp=timestamp_s)

            if isinstance(frame, rtc.AudioFrame):
                self._playback_position += frame.duration
