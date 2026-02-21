from __future__ import annotations, print_function

import asyncio
import os

import aiohttp
from livekit import api, rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    AgentSession,
    AgentStateChangedEvent,
    APIConnectionError,
    APIStatusError,
    NotGivenOr,
    UserStateChangedEvent,
    get_job_context,
    utils,
)
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF
from livekit.agents.voice.avatar import (
    DataStreamAudioOutput,
)

from .log import logger


class AvalumaException(Exception):
    """Exception for Avaluma errors"""


class AvatarSession:
    def __init__(
        self,
        license_key: str,
        avatar_id: str,
        avatar_server_url: str = "https://api.avaluma.ai",
    ):
        self._license_key = license_key
        self._avatar_id = avatar_id
        self._avatar_server_url = avatar_server_url

        self._conn_options = DEFAULT_API_CONNECT_OPTIONS
        self._http_session = utils.http_context.http_session()

    async def start(self, room: rtc.Room, agent_session: AgentSession):
        livekit_url = os.getenv("LIVEKIT_URL") or None
        livekit_api_key = os.getenv("LIVEKIT_API_KEY") or None
        livekit_api_secret = os.getenv("LIVEKIT_API_SECRET") or None

        if not livekit_url or not livekit_api_key or not livekit_api_secret:
            raise AvalumaException(
                "livekit_url, livekit_api_key, and livekit_api_secret must be set "
                "by environment variables"
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

        self._avatar_participant_name = f"avatar-{self._avatar_id}"
        self._avatar_participant_identity = f"avatar-{self._avatar_id}"

        livekit_token = (
            api.AccessToken(api_key=livekit_api_key, api_secret=livekit_api_secret)
            .with_kind("agent")
            .with_identity(self._avatar_participant_identity)
            .with_name(self._avatar_participant_name)
            .with_grants(api.VideoGrants(room_join=True, room=room.name))
            # allow the avatar agent to publish audio and video on behalf of your local agent
            .with_attributes(
                {
                    ATTRIBUTE_PUBLISH_ON_BEHALF: local_participant_identity,
                }
            )
            .to_jwt()
        )

        await self._request_remote_avatar_to_join(livekit_url, livekit_token, room.name)

        # Register turn taking event handlers
        self.register_turn_taking_event(agent_session, room)

        # Register shutdown callback to stop remote avatar
        try:
            job_ctx = get_job_context()

            async def _on_shutdown() -> None:
                await self._request_remote_avatar_to_stop()

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
            "avaluma_key": self._license_key,
            "avaluma_avatar_id": self._avatar_id,
        }

        for i in range(self._conn_options.max_retry):
            try:
                async with self._http_session.post(
                    self._avatar_server_url + "/v1/livekit/start-avatar",
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

        if not self._avatar_server_url:
            logger.warning("Cannot stop remote avatar: hvi_server_url not set")
            return

        # Build stop URL from start URL
        stop_url = self._avatar_server_url + "/v1/livekit/stop-avatar"

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

        # Clear the avatar server URL
        self._avatar_server_url = None

        # Clear the avatar server URL
        self._avatar_server_url = None

    def register_turn_taking_event(self, session: AgentSession, room: rtc.Room):

        @session.on("user_state_changed")
        def on_user_state_changed(ev: UserStateChangedEvent):
            if ev.new_state == "speaking":
                print("User started speaking")
            elif ev.new_state == "listening":
                print("User stopped speaking")
            elif ev.new_state == "away":
                print("User is not present (e.g. disconnected)")

        @session.on("agent_state_changed")
        async def on_agent_state_changed(ev: AgentStateChangedEvent):
            if ev.new_state == "initializing":
                print("Agent is starting up")
            elif ev.new_state == "idle":
                print("Agent is ready but not processing")
            elif ev.new_state == "listening":
                print("Agent is listening for user input")
            elif ev.new_state == "thinking":
                print("Agent is processing user input and generating a response")
            elif ev.new_state == "speaking":
                print("Agent started speaking")

            await room.local_participant.perform_rpc(
                destination_identity=self._avatar_participant_identity,
                method="agent_state_changed",
                payload=ev.new_state,
            )
