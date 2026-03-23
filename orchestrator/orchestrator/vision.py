"""Vision module for Star Citizen HUD analysis.

Captures screen regions of interest (ROIs) and sends them to Claude Vision
for structured HUD data extraction. Supports activity-aware ROI filtering
so only relevant HUD elements are analyzed for the current game state.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
import mss
import yaml
from PIL import Image

from .game_state import GameActivity

logger = logging.getLogger(__name__)


@dataclass
class ROIDefinition:
    """A rectangular region of interest on the game screen."""

    name: str
    x: int
    y: int
    width: int
    height: int
    description: str
    active_activities: list[GameActivity] = field(default_factory=list)


def load_roi_definitions(yaml_path: str) -> list[ROIDefinition]:
    """Load ROI definitions from a YAML file.

    The YAML file should contain a top-level ``rois`` list, where each entry
    has keys matching :class:`ROIDefinition` fields.  The
    ``active_activities`` values are strings that map to :class:`GameActivity`
    enum members.
    """
    with open(yaml_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    definitions: list[ROIDefinition] = []
    for entry in data.get("rois", []):
        activities = [
            GameActivity(a) for a in entry.get("active_activities", [])
        ]
        definitions.append(
            ROIDefinition(
                name=entry["name"],
                x=entry["x"],
                y=entry["y"],
                width=entry["width"],
                height=entry["height"],
                description=entry.get("description", ""),
                active_activities=activities,
            )
        )
    return definitions


class VisionModule:
    """Captures screen frames and analyses HUD via Claude Vision.

    Parameters
    ----------
    capture_fps:
        How many screen captures per second in the background loop.
    roi_definitions:
        Pre-loaded ROI rectangles.  If *None* an empty list is used.
    anthropic_api_key:
        API key for Anthropic.  When empty, vision analysis is skipped
        and empty results are returned (graceful degradation).
    vision_model:
        Model identifier passed to the Anthropic messages API.
    """

    def __init__(
        self,
        capture_fps: int = 1,
        roi_definitions: list[ROIDefinition] | None = None,
        anthropic_api_key: str = "",
        vision_model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._capture_fps = max(1, capture_fps)
        self._roi_definitions: list[ROIDefinition] = roi_definitions or []
        self._roi_map: dict[str, ROIDefinition] = {
            r.name: r for r in self._roi_definitions
        }
        self._vision_model = vision_model

        # Anthropic async client -- created lazily so missing key doesn't
        # explode at import time.
        self._api_key = anthropic_api_key
        self._client: anthropic.AsyncAnthropic | None = None

        # Background capture state
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._latest_analysis: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def latest_analysis(self) -> dict[str, Any] | None:
        """Most recent HUD analysis result, or *None* if nothing yet."""
        return self._latest_analysis

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background capture loop."""
        if self._running:
            logger.warning("VisionModule already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info(
            "VisionModule started (capture_fps=%d)", self._capture_fps
        )

    async def stop(self) -> None:
        """Stop the background capture loop and clean up."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("VisionModule stopped")

    # ------------------------------------------------------------------
    # Capture helpers
    # ------------------------------------------------------------------

    async def capture_full_frame(self) -> str | None:
        """Capture the full primary monitor as a base64-encoded JPEG.

        Returns *None* if the capture fails.
        """
        try:
            return await asyncio.to_thread(self._sync_capture_full_frame)
        except Exception:
            logger.exception("Full-frame capture failed")
            return None

    async def capture_roi(self, roi_name: str) -> str | None:
        """Capture a specific ROI as a base64-encoded JPEG.

        Returns *None* if the ROI name is unknown or capture fails.
        """
        roi = self._roi_map.get(roi_name)
        if roi is None:
            logger.warning("Unknown ROI: %s", roi_name)
            return None
        try:
            return await asyncio.to_thread(self._sync_capture_roi, roi)
        except Exception:
            logger.exception("ROI capture failed for %s", roi_name)
            return None

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    async def analyze_hud(
        self, activity: GameActivity
    ) -> dict[str, Any]:
        """Capture and analyse all ROIs relevant to *activity*.

        For each ROI whose ``active_activities`` list includes *activity*
        (or whose list is empty, meaning always-on), the region is
        captured and sent to Claude Vision as a single multi-image
        request.  The model is asked to return structured JSON
        describing the HUD state.

        Returns an empty dict on any failure (graceful degradation).
        """
        relevant_rois = [
            r
            for r in self._roi_definitions
            if not r.active_activities or activity in r.active_activities
        ]
        if not relevant_rois:
            logger.debug("No ROIs relevant for activity=%s", activity)
            return {}

        # Capture all relevant regions in parallel
        captures: dict[str, str] = {}
        tasks = {
            roi.name: asyncio.create_task(self.capture_roi(roi.name))
            for roi in relevant_rois
        }
        for name, task in tasks.items():
            result = await task
            if result is not None:
                captures[name] = result

        if not captures:
            logger.warning("All ROI captures failed for activity=%s", activity)
            return {}

        # Build multi-image content for Claude
        content: list[dict[str, Any]] = []
        for name, b64 in captures.items():
            roi = self._roi_map[name]
            content.append(
                {"type": "text", "text": f"[{name}] {roi.description}"}
            )
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                }
            )

        prompt = (
            "You are analysing Star Citizen HUD screenshots. "
            f"The player's current activity is: {activity.value}.\n"
            "For each labelled image, extract all visible data "
            "(numbers, statuses, indicators) and return a single JSON "
            "object keyed by region name. Use null for any value you "
            "cannot confidently read."
        )
        content.append({"type": "text", "text": prompt})

        try:
            client = self._get_client()
            if client is None:
                return {}

            response = await client.messages.create(
                model=self._vision_model,
                max_tokens=2048,
                messages=[{"role": "user", "content": content}],
            )
            text = response.content[0].text

            # Attempt to parse JSON from the response
            import json

            # Strip markdown fences if present
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            result = json.loads(cleaned)
            self._latest_analysis = result
            return result
        except Exception:
            logger.exception("HUD analysis failed for activity=%s", activity)
            return {}

    async def analyze_frame(self, image_b64: str, prompt: str) -> str:
        """General-purpose vision query against a single image.

        Returns an empty string on failure (graceful degradation).
        """
        try:
            client = self._get_client()
            if client is None:
                return ""

            response = await client.messages.create(
                model=self._vision_model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            return response.content[0].text
        except Exception:
            logger.exception("Frame analysis failed")
            return ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> anthropic.AsyncAnthropic | None:
        """Return a cached async Anthropic client, or *None* if no key."""
        if not self._api_key:
            logger.debug("No Anthropic API key configured; skipping vision")
            return None
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _sync_capture_full_frame() -> str:
        """Blocking helper: grab full primary monitor -> base64 JPEG."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _sync_capture_roi(roi: ROIDefinition) -> str:
        """Blocking helper: grab a specific ROI rectangle -> base64 JPEG."""
        region = {
            "left": roi.x,
            "top": roi.y,
            "width": roi.width,
            "height": roi.height,
        }
        with mss.mss() as sct:
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("ascii")

    async def _capture_loop(self) -> None:
        """Background loop that periodically captures the full frame."""
        interval = 1.0 / self._capture_fps
        while self._running:
            try:
                frame = await self.capture_full_frame()
                if frame is not None:
                    logger.debug(
                        "Background capture OK (%d bytes)",
                        len(frame),
                    )
            except Exception:
                logger.exception("Error in capture loop")
            await asyncio.sleep(interval)
