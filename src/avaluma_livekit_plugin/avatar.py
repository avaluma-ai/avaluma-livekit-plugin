from __future__ import annotations, print_function

import asyncio
import os

import aiohttp
import numpy as np
from livekit import api, rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    Agent,
    AgentSession,
    APIConnectionError,
    APIStatusError,
    NotGivenOr,
    get_job_context,
    utils,
)
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF
from livekit.agents.voice.avatar import (
    AvatarOptions,
    DataStreamAudioOutput,
    QueueAudioOutput,
)

from .local.resources import (
    InsufficientResourcesError,
    ResourceMonitor,
    ResourceThresholds,
)
from .log import logger
from .utils import delete_all_ingress_for_room, mute_track_for_user


class AvalumaException(Exception):
    """Exception for Avaluma errors"""


class LocalAvatarSession:
    def __init__(
        self,
        license_key: str,
        avatar_id: str,
        assets_dir: str,
        check_resources: bool = True,
    ):
        # TODO: check if local/bin is not empty
        if not os.path.exists(os.path.join(os.path.dirname(__file__), "local/bin")):
            raise AvalumaException("local/bin directory not found")

        asset_path = os.path.join(assets_dir, f"{avatar_id}.hvia")
        if avatar_id is None:
            raise AvalumaException("avatar_id cannot be None")
        elif not os.path.exists(asset_path):
            raise AvalumaException(f"Asset file not found: {asset_path}")

        kwargs = {
            "license_key": license_key,
            "avatar_id": avatar_id,
            "asset_path": asset_path,
        }

        from .local.avatar_cpp_wrapper import AvalumaRuntime

        self._runtime = AvalumaRuntime(**kwargs)
        self._audio_buffer = QueueAudioOutput(
            sample_rate=self._runtime.settings.INPUT_SAMPLE_RATE
        )
        self._ingress_task: asyncio.Task | None = None

        if check_resources:
            thresholds = ResourceThresholds()
            self._resource_monitor = ResourceMonitor(thresholds)
            logger.info(
                f"Resource monitor initialized with thresholds: "
                f"RAM>={thresholds.min_free_ram_gb}GB, VRAM>={thresholds.min_free_vram_gb}GB"
            )
        else:
            logger.info("Resource monitoring is disabled")

    async def start(
        self, room: rtc.Room, agent_session: NotGivenOr[AgentSession] = NOT_GIVEN
    ) -> None:
        if self._resource_monitor:
            await self.run_resource_check(room)

        from .local.video_generator import AvalumaVideoGenerator

        video_generator = AvalumaVideoGenerator(self._runtime)

        output_width, output_height = video_generator.video_resolution
        avatar_options = AvatarOptions(
            video_width=output_width,
            video_height=output_height,
            video_fps=video_generator.video_fps,
            audio_sample_rate=video_generator.audio_sample_rate,
            audio_channels=1,
        )

        # create avatar runner
        from .local.avatar_runner import AvalumaAvatarRunner

        self._avatar_runner = AvalumaAvatarRunner(
            room=room,
            video_gen=video_generator,
            audio_recv=self._audio_buffer,
            options=avatar_options,
        )
        await self._avatar_runner.start()

        if agent_session:
            agent_session.output.audio = self._audio_buffer
        else:
            # Importent otherwise the agent video stops after 10s
            ctx = get_job_context()
            session = AgentSession()
            await session.start(
                agent=Agent(instructions=""),
                room=ctx.room,
            )

        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.TrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            """Synchronous callback, starts an async task to process the audio."""
            if (
                track.kind == rtc.TrackKind.KIND_AUDIO
                and "external-agent-ingress" in participant.identity
            ):
                logger.info(
                    f"Subscribed to ingress audio track from {participant.identity}, starting processor task."
                )
                asyncio.create_task(mute_track_for_user(track, room))
                # Cast is safe due to the kind check above
                asyncio.create_task(self.process_ingress_track(track))  # type: ignore

        room.on("track_subscribed", on_track_subscribed)

        try:
            job_ctx = get_job_context()

            async def _on_shutdown() -> None:
                self._runtime.cleanup()
                await delete_all_ingress_for_room(room)

            job_ctx.add_shutdown_callback(_on_shutdown)
        except RuntimeError:
            logger.error("Failed to register shutdown callback")
            pass

    async def stop(self):
        await self._runtime.stop()

    async def process_ingress_track(self, track: rtc.AudioTrack):
        """Process audio frames from the ingress track and push them to the avatar.

        If a previous ingress track is being processed, it will be cancelled
        so only the most recent track is used.
        """
        # Cancel previous ingress task if it exists
        if self._ingress_task and not self._ingress_task.done():
            logger.info("Cancelling previous ingress track processing task")
            self._ingress_task.cancel()
            try:
                await self._ingress_task
            except asyncio.CancelledError:
                pass

        # Store reference to current task
        self._ingress_task = asyncio.current_task()

        try:
            audio_stream = rtc.AudioStream(track)

            # No VAD - fall back to RMS-based silence detection
            print("using RMS-based silence detection")

            SILENCE_THRESHOLD = 100  # RMS threshold for silence (adjust as needed)
            SILENCE_FRAMES_REQUIRED = 10  # ~0.5s at 50fps before flushing

            silence_frame_count = 0
            is_speaking = False

            async for frame_event in audio_stream:
                frame = frame_event.frame
                # print(
                #     f"SampleRate: {frame.sample_rate}, Channels: {frame.num_channels}, SamplesPerChannel: {frame.samples_per_channel}, Length: {len(frame.data)}"
                # )
                # Convert bytes to int16 samples and calculate RMS
                samples = np.frombuffer(frame.data, dtype=np.int16)
                rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))

                if rms < SILENCE_THRESHOLD:
                    silence_frame_count += 1
                    if is_speaking and silence_frame_count >= SILENCE_FRAMES_REQUIRED:
                        logger.debug(
                            f"🔇 Silence detected (RMS={rms:.1f}, frames={silence_frame_count}) -> flushing"
                        )
                        self._audio_buffer.flush()
                        is_speaking = False
                    # Don't capture silence frames after flush
                    if not is_speaking:
                        continue
                else:
                    # Audio detected
                    if not is_speaking:
                        logger.debug(
                            f"🔊 Audio detected (RMS={rms:.1f}) -> starting capture"
                        )
                        is_speaking = True
                    silence_frame_count = 0

                await self._audio_buffer.capture_frame(frame)
        except asyncio.CancelledError:
            logger.info("Ingress track processing cancelled")
            raise

    async def run_resource_check(self, room: rtc.Room):
        logger.info("Performing resource check...")
        try:
            status = self._resource_monitor.check_resources()
            result_msg = f"Resource check passed: RAM={status.available_ram_gb:.2f}GB"
            if status.available_vram_gb is not None:
                result_msg += (
                    f", VRAM={status.available_vram_gb:.2f}GB (GPU: {status.gpu_name})"
                )
            logger.info(result_msg)
        except InsufficientResourcesError as e:
            error_msg = f"Cannot create avatar '{self._avatar_id}': {e}"
            logger.error(error_msg)

            # Communicate error to frontend via participant attributes (if room is connected)
            try:
                if room.isconnected():
                    await room.local_participant.set_attributes(
                        {
                            "error": "insufficient_resources",
                            "error_message": str(e),
                            "available_vram_gb": str(e.available_vram_gb or ""),
                            "required_vram_gb": str(e.required_vram_gb or ""),
                            "available_ram_gb": str(e.available_ram_gb or ""),
                            "required_ram_gb": str(e.required_ram_gb or ""),
                        }
                    )
            except Exception as attr_error:
                logger.warning(f"Could not set participant attributes: {attr_error}")

            logger.error(error_msg, exc_info=True)
            raise


class RemoteAvatarSession:
    def __init__(self, license_key: str, avatar_id: str, avalume_server_url: str):
        self._license_key = license_key
        self._avatar_id = avatar_id
        self._avaluma_server_url = avalume_server_url

        self._conn_options = DEFAULT_API_CONNECT_OPTIONS
        self._http_session = utils.http_context.http_session()

    async def start(
        self,
        room: rtc.Room,
        agent_session: NotGivenOr[AgentSession] = NOT_GIVEN,
    ):
        livekit_url = os.getenv("LIVEKIT_URL") or None
        livekit_api_key = os.getenv("LIVEKIT_API_KEY") or None
        livekit_api_secret = os.getenv("LIVEKIT_API_SECRET") or None

        if not livekit_url or not livekit_api_key or not livekit_api_secret:
            raise AvalumaException(
                "livekit_url, livekit_api_key, and livekit_api_secret must be set "
                "by arguments or environment variables"
            )

        # Get local participant identity
        try:
            job_ctx = get_job_context()
            local_participant_identity = job_ctx.token_claims().identity
        except RuntimeError as e:
            if not room.isconnected():
                raise AvalumaException(
                    "failed to get local participant identity"
                ) from e
            local_participant_identity = room.local_participant.identity

        # Prepare attributes for JWT token
        attributes: dict[str, str] = {
            ATTRIBUTE_PUBLISH_ON_BEHALF: local_participant_identity,
            # "avaluma_license_key": self._license_key,
            # "avaluma_avatar_id": self._avatar_id,
        }

        self._avatar_participant_name = f"Avatar-{self._avatar_id}"
        self._avatar_participant_identity = f"avatar-{self._avatar_id}"

        livekit_token = (
            api.AccessToken(api_key=livekit_api_key, api_secret=livekit_api_secret)
            .with_kind("agent")
            .with_identity(self._avatar_participant_identity)
            .with_name(self._avatar_participant_name)
            .with_grants(api.VideoGrants(room_join=True, room=room.name))
            # allow the avatar agent to publish audio and video on behalf of your local agent
            .with_attributes(attributes)
            .to_jwt()
        )

        await self._request_remote_avatar_to_join(livekit_url, livekit_token, room.name)

        # Register shutdown callback to stop remote avatar
        try:
            job_ctx = get_job_context()

            async def _on_shutdown() -> None:
                await self.stop()

            job_ctx.add_shutdown_callback(_on_shutdown)
        except RuntimeError:
            pass

        if agent_session is not None:
            agent_session.output.audio = DataStreamAudioOutput(
                room=room,
                destination_identity=self._avatar_participant_identity,
                sample_rate=16000,
                wait_remote_track=rtc.TrackKind.KIND_VIDEO,
            )

    async def _request_remote_avatar_to_join(
        self, livekit_url: str, livekit_token: str, room_name: str
    ):
        # Prepare JSON data
        json_data = {
            "livekit_url": livekit_url,
            "livekit_token": livekit_token,
            "livekit_room_name": room_name,
            "avaluma_license_key": self._license_key,
            "avaluma_avatar_id": self._avatar_id,
        }

        for i in range(self._conn_options.max_retry):
            try:
                async with self._http_session.post(
                    self._avaluma_server_url + "/v1/livekit/start-avatar",
                    headers={
                        "Content-Type": "application/json",
                        "api-secret": self._license_key,
                    },
                    json=json_data,
                    timeout=aiohttp.ClientTimeout(
                        sock_connect=self._conn_options.timeout
                    ),
                ) as response:
                    if not response.ok:
                        text = await response.text()
                        raise APIStatusError(
                            "Server returned an error",
                            status_code=response.status,
                            body=text,
                        )

                    # Try to get session_id from response
                    try:
                        response_data = await response.json()
                        self._session_id = response_data.get("session_id")
                        if self._session_id:
                            logger.debug(
                                f"Remote avatar session started: {self._session_id}"
                            )
                    except Exception:
                        # Response might not be JSON, that's ok
                        pass

                    return

            except Exception as e:
                if isinstance(e, APIConnectionError):
                    logger.warning(
                        "failed to call avaluma avatar api", extra={"error": str(e)}
                    )
                else:
                    logger.exception("failed to call avaluma avatar api")

                if i < self._conn_options.max_retry - 1:
                    await asyncio.sleep(self._conn_options.retry_interval)

        raise APIConnectionError(
            "Failed to start Avaluma Avatar Session after all retries"
        )

    async def _request_remote_avatar_to_stop(self) -> None:
        """Request the remote avatar to leave the room."""
        if not self._session_id:
            return

        if not self._avaluma_server_url:
            logger.warning("Cannot stop remote avatar: hvi_server_url not set")
            return

        # Build stop URL from start URL
        stop_url = self._avaluma_server_url + "/v1/livekit/stop-avatar"

        logger.debug(f"Stopping remote avatar session: {self._session_id}")

        try:
            async with self._http_session.post(
                stop_url,
                headers={
                    "Content-Type": "application/json",
                    "api-secret": self._license_key,
                },
                json={"session_id": self._session_id},
                timeout=aiohttp.ClientTimeout(sock_connect=5.0),
            ) as response:
                if not response.ok:
                    text = await response.text()
                    logger.warning(
                        f"Failed to stop remote avatar: {response.status} - {text}"
                    )
                else:
                    logger.debug("Remote avatar session stopped successfully")
        except Exception as e:
            logger.warning(f"Error stopping remote avatar: {e}")
        finally:
            self._session_id = None
