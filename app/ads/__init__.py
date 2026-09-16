from __future__ import annotations


class AdsPowerError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.code = code
        self.payload = payload or {}

    @property
    def is_quota(self) -> bool:
        text = str(self).lower()
        needles = ("limit", "quota", "exceed", "maximum", "max profile", "not enough")
        return any(n in text for n in needles)

    @property
    def is_timeout(self) -> bool:
        if self.payload.get("timeout"):
            return True
        text = str(self).lower()
        return any(
            needle in text
            for needle in (
                "timed out",
                "timeout",
                "100044",
                "waiting for browser to start",
            )
        )

    @property
    def is_already_open(self) -> bool:
        text = str(self).lower()
        return any(
            needle in text
            for needle in (
                "already open",
                "already started",
                "already running",
                "browser is running",
                "has been opened",
                "profile is active",
            )
        )

    @property
    def is_kernel_download(self) -> bool:
        text = str(self).lower()
        if "sunbrowser" in text and any(n in text for n in ("install", "updat", "download", "waiting")):
            return True
        return any(
            needle in text
            for needle in (
                "waiting for download",
                "is updating, waiting",
                "downloading",
                "download kernel",
                "kernel is updating",
                "being installed",
                "is being installed",
                "is not ready",
                "please to download",
            )
        )

    @property
    def is_start_busy(self) -> bool:
        return self.is_kernel_download or self.is_timeout or self.is_already_open
