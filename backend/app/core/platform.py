"""macOS-only platform facade.

The original weix project shipped Windows and macOS branches. weizx is
dedicated to the macOS implementation; Windows code paths have been
removed. See README for the fork rationale.
"""

from typing import Any

from app.config import get_config


class Platform:
    """Load the macOS implementation."""

    _instance: "Platform | None" = None

    def __init__(self):
        config = get_config()
        self.name = config.get_platform()
        self._sender = None
        self._key_extractor = None
        self._db_reader = None

    @classmethod
    def get(cls) -> "Platform":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached singleton. Tests use this to swap configs."""
        cls._instance = None

    @property
    def is_macos(self) -> bool:
        return True

    @property
    def sender(self) -> Any:
        if self._sender is None:
            from app.core.sender_macos import MacOSSender
            from app.core.vision_client import VisionClient
            api_key = str(get_config().ai.get("api_key", "") or "").strip()
            vision = VisionClient(api_key) if api_key else None
            self._sender = MacOSSender(vision_client=vision)
        return self._sender

    @property
    def key_extractor(self) -> Any:
        if self._key_extractor is None:
            from app.core.key_extractor_macos import MacOSKeyExtractor
            self._key_extractor = MacOSKeyExtractor()
        return self._key_extractor

    @property
    def db_reader(self) -> Any:
        if self._db_reader is None:
            from app.core.db_reader_macos import MacOSDBReader
            self._db_reader = MacOSDBReader()
        return self._db_reader