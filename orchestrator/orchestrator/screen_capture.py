"""Optional screen capture module for game window analysis via Claude vision."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time

logger = logging.getLogger(__name__)


class CaptureManager:
    """Captures the game window at a configurable frame rate.

    Runs a background loop that grabs screenshots and keeps the latest
    frame available for on-demand retrieval. Resizes captures to 720p
    to manage token usage with Claude's vision API.
    """

    TARGET_WIDTH = 1280
    TARGET_HEIGHT = 720

    def __init__(self, fps: int = 1, enabled: bool = False) -> None:
        self._fps = max(1, fps)
        self._enabled = enabled
        self._latest_frame: str | None = None  # base64 JPEG
        self._latest_timestamp: float = 0.0
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def latest_frame(self) -> str | None:
        return self._latest_frame

    @property
    def latest_timestamp(self) -> float:
        return self._latest_timestamp

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Screen capture disabled")
            return

        try:
            import mss  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            logger.warning("mss or Pillow not installed; screen capture unavailable")
            self._enabled = False
            return

        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info("Screen capture started at %d FPS", self._fps)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Screen capture stopped")

    async def get_frame_base64(self) -> str | None:
        """Return the latest captured frame as a base64-encoded JPEG string."""
        if not self._enabled or self._latest_frame is None:
            return None
        return self._latest_frame

    async def capture_once(self) -> str | None:
        """Capture a single frame immediately and return as base64 JPEG."""
        if not self._enabled:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._grab_frame)

    async def _capture_loop(self) -> None:
        interval = 1.0 / self._fps
        loop = asyncio.get_running_loop()
        while self._running:
            frame = await loop.run_in_executor(None, self._grab_frame)
            if frame:
                self._latest_frame = frame
                self._latest_timestamp = time.time()
            await asyncio.sleep(interval)

    def _grab_frame(self) -> str | None:
        """Grab a screenshot, resize to 720p, and encode as base64 JPEG."""
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                # Grab the primary monitor (or find game window specifically)
                monitor = sct.monitors[1]  # Primary monitor
                screenshot = sct.grab(monitor)

                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                img = img.resize(
                    (self.TARGET_WIDTH, self.TARGET_HEIGHT),
                    Image.LANCZOS,
                )

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=75)
                buf.seek(0)
                return base64.b64encode(buf.read()).decode("ascii")

        except Exception:
            logger.debug("Screen capture failed", exc_info=True)
            return None
