"""
Avaluma plugin for LiveKit Agents
"""

from .avatar import AvalumaException, AvatarSession, LocalAvatarSession
from .version import __version__

__all__ = [
    "AvalumaException",
    "AvatarSession",
    "LocalAvatarSession",
    "__version__",
]

from livekit.agents import Plugin

from .log import logger


class AvalumaPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(AvalumaPlugin())

# Cleanup docs of unexported modules
_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__ = {}

for n in NOT_IN_ALL:
    __pdoc__[n] = False
