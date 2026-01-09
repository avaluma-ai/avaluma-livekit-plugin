"""
Avaluma plugin for LiveKit Agents
"""

from .avatar import AvalumaException, LocalAvatarSession, RemoteAvatarSession
from .legacy_avatar import AvatarSession
from .local.bin_downloader import BinDownloader
from .version import __version__

__all__ = [
    "AvalumaException",
    "AvatarSession",
    "LocalAvatarSession",
    "RemoteAvatarSession",
    "__version__",
]

from livekit.agents import Plugin

from .log import logger


class AvalumaPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)

    def download_files(self):
        logger.info("Downloading files for avaluma plugin")
        BinDownloader()
        logger.info("Files for avaluma downloaded successfully")


Plugin.register_plugin(AvalumaPlugin())

# Cleanup docs of unexported modules
_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__ = {}

for n in NOT_IN_ALL:
    __pdoc__[n] = False
