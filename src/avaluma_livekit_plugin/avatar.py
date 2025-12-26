from __future__ import annotations, print_function

import asyncio
import os

import aiohttp
from livekit import api, rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
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
    AvatarRunner,
    DataStreamAudioOutput,
    QueueAudioOutput,
)

from .local.avatar_cpp_wrapper import AvalumaRuntime, AvatarSession as CppAvatarSession

# from PIL import Image
from .log import logger


class AvalumaException(Exception):
    """Exception for Avaluma errors"""


class LocalAvatarSession:
    """
    Simplified avatar session for local mode only.

    This is a convenience wrapper around AvatarSession that:
    - Only supports local mode (no remote server)
    - Has a simpler constructor (no mode/hvi_server_url params)
    - Provides a cleaner start() signature

    Usage:
        avatar = LocalAvatarSession(
            license_key="...",
            avatar_id="my-avatar",
            assets_dir="/path/to/avatars",
        )
        await avatar.start(agent_session=session, room=ctx.room)
    """

    def __init__(
        self,
        *,
        license_key: NotGivenOr[str] = NOT_GIVEN,
        avatar_id: NotGivenOr[str] = NOT_GIVEN,
        assets_dir: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        """
        Initialize a local avatar session.

        Args:
            license_key: The Avaluma License Key (or AVALUMA_LICENSE_KEY env).
            avatar_id: The avatar ID to use.
            assets_dir: Directory containing avatar assets (or AVALUMA_ASSETS_DIR env).
        """
        # Defer creation of AvatarSession until we verify it will work
        self._license_key = license_key
        self._avatar_id = avatar_id
        self._assets_dir = assets_dir
        self._session: AvatarSession | None = None

    async def start(
        self,
        agent_session: AgentSession,
        room: rtc.Room,
    ) -> None:
        """
        Start the local avatar session.

        Args:
            agent_session: The LiveKit agent session
            room: The LiveKit room
        """
        # Create the underlying AvatarSession on first start
        if self._session is None:
            self._session = AvatarSession(
                license_key=self._license_key,
                avatar_id=self._avatar_id,
                assets_dir=self._assets_dir,
                mode="local",
            )

        await self._session.start(agent_session, room)

    @property
    def session(self):
        """Access the underlying C++ avatar session."""
        if self._session is None:
            raise AvalumaException("Session not started - call start() first")
        return self._session.session

    @property
    def runtime(self):
        """Access the singleton AvalumaRuntime."""
        return AvalumaRuntime()


class AvatarSession:
    def __init__(
        self,
        *,
        license_key: NotGivenOr[str] = NOT_GIVEN,
        avatar_id: NotGivenOr[str] = NOT_GIVEN,
        assets_dir: NotGivenOr[str] = NOT_GIVEN,
        hvi_server_url: NotGivenOr[str] = NOT_GIVEN,
        mode: str = "local",
    ) -> None:
        """
        Initialize a Avaluma avatar session.

        Args:
            license_key: The Avaluma License Key.
            avatar_id: The avatar ID to use.
        """
        self._license_key = license_key or os.getenv("AVALUMA_LICENSE_KEY")
        self._avatar_id = avatar_id
        self._assets_dir = assets_dir or os.getenv("AVALUMA_ASSETS_DIR")
        self._avaluma_hvi_server_url = hvi_server_url or os.getenv(
            "AVALUMA_HVI_SERVER_URL"
        )
        self._mode = mode
        self._conn_options = DEFAULT_API_CONNECT_OPTIONS

        if self._license_key is None:
            raise AvalumaException("`license_key` or AVALUMA_LICENSE_KEY are required")
        if self._avatar_id is None:
            raise AvalumaException("`avatar_id` is required")

        # validate mode-specific requirements
        if self._mode == "local":
            if self._assets_dir is None:
                raise AvalumaException(
                    "`assets_dir` or AVALUMA_ASSETS_DIR env must be set for local mode"
                )
            # TODO: check if local/bin is not empty
            if not os.path.exists(os.path.join(os.path.dirname(__file__), "local/bin")):
                raise AvalumaException("local/bin directory not found")
        elif self._mode == "remote":
            if self._avaluma_hvi_server_url is None:
                raise AvalumaException(
                    "`avaluma_hvi_server_url` or AVALUMA_HVI_SERVER_URL env must be set for remote mode"
                )
        else:
            raise AvalumaException(f"Unknown mode: {self._mode}")

        self._http_session: aiohttp.ClientSession | None = None
        self._avatar_runner: AvatarRunner | None = None
        self._cpp_session: CppAvatarSession | None = None

    async def start(
        self,
        agent_session: AgentSession,
        room: rtc.Room,
        *,
        livekit_url: NotGivenOr[str] = NOT_GIVEN,
        livekit_api_key: NotGivenOr[str] = NOT_GIVEN,
        livekit_api_secret: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        if self._mode == "local":
            await self._start_local(agent_session, room)
        elif self._mode == "remote":
            await self._start_remote(
                agent_session,
                room,
                livekit_url=livekit_url,
                livekit_api_key=livekit_api_key,
                livekit_api_secret=livekit_api_secret,
            )
        else:
            raise AvalumaException(f"Invalid mode: {self._mode}")

    async def _start_local(self, agent_session: AgentSession, room: rtc.Room) -> None:
        if not self._cpp_session:
            if self._assets_dir is None:
                raise ValueError("assets_dir is not set")

            asset_path = os.path.join(self._assets_dir, f"{self._avatar_id}.hvia")

            # Get singleton runtime and create session for this avatar
            runtime = AvalumaRuntime()
            self._cpp_session = runtime.create_session(asset_path=asset_path)
            logger.info(f"Created avatar session: {self._cpp_session.session_id}")
        else:
            logger.info("Avatar session already initialized")

        session = self._cpp_session

        from .local import AvalumaVideoGenerator

        video_generator = AvalumaVideoGenerator(session)

        try:
            job_ctx = get_job_context()

            async def _on_shutdown() -> None:
                session.cleanup()

            job_ctx.add_shutdown_callback(_on_shutdown)
        except RuntimeError:
            pass

        output_width, output_height = video_generator.video_resolution
        avatar_options = AvatarOptions(
            video_width=output_width,
            video_height=output_height,
            video_fps=video_generator.video_fps,
            audio_sample_rate=video_generator.audio_sample_rate,
            audio_channels=1,
        )

        audio_buffer = QueueAudioOutput(sample_rate=session.settings.INPUT_SAMPLE_RATE)
        # create avatar runner
        from .local.avatar_runner import AvalumaAvatarRunner

        self._avatar_runner = AvalumaAvatarRunner(
            room=room,
            video_gen=video_generator,
            audio_recv=audio_buffer,
            options=avatar_options,
        )
        await self._avatar_runner.start()

        agent_session.output.audio = audio_buffer

    async def _start_remote(
        self,
        agent_session: AgentSession,
        room: rtc.Room,
        *,
        livekit_url: NotGivenOr[str] = NOT_GIVEN,
        livekit_api_key: NotGivenOr[str] = NOT_GIVEN,
        livekit_api_secret: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        livekit_url = livekit_url or (os.getenv("LIVEKIT_URL") or NOT_GIVEN)
        livekit_api_key = livekit_api_key or (os.getenv("LIVEKIT_API_KEY") or NOT_GIVEN)
        livekit_api_secret = livekit_api_secret or (
            os.getenv("LIVEKIT_API_SECRET") or NOT_GIVEN
        )
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

        logger.debug("starting avatar session")
        await self._request_remote_avatar_to_join(livekit_url, livekit_token, room.name)
        agent_session.output.audio = DataStreamAudioOutput(
            room=room,
            destination_identity=self._avatar_participant_identity,
        )

    async def _request_remote_avatar_to_join(
        self, livekit_url: str, livekit_token: str, room_name: str
    ):
        if self._license_key is None:
            raise ValueError("license_key is not set")

        # Prepare JSON data
        json_data = {
            "livekit_url": livekit_url,
            "livekit_token": livekit_token,
            "livekit_room_name": room_name,
            "avaluma_license_key": self._license_key,
            "avaluma_avatar_id": self._avatar_id,
        }

        assert self._avaluma_hvi_server_url is not None, "api_url is not set"
        # assert self._api_secret is not None, "api_secret is not set"

        for i in range(self._conn_options.max_retry):
            try:
                async with self._ensure_http_session().post(
                    self._avaluma_hvi_server_url,
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

    def _ensure_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None:
            self._http_session = utils.http_context.http_session()

        return self._http_session

    @property
    def session(self) -> CppAvatarSession:
        """Get the underlying C++ avatar session."""
        if self._cpp_session is None:
            raise AvalumaException("Session not initialized - call start() first")
        return self._cpp_session

    @property
    def runtime(self) -> AvalumaRuntime:
        """Get the singleton AvalumaRuntime."""
        return AvalumaRuntime()
