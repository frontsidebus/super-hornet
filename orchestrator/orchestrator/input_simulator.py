"""EAC-safe input simulation for Super Hornet.

╔══════════════════════════════════════════════════════════════════════╗
║  SAFETY WARNING                                                      ║
║                                                                      ║
║  This module sends real keystrokes and mouse events to the OS.       ║
║  All actions are logged to an internal audit trail.  The simulator   ║
║  is DISABLED by default — no inputs are ever sent unless the         ║
║  caller explicitly sets ``enabled=True``.                            ║
║                                                                      ║
║  • Never enable this in automated tests without a human operator.    ║
║  • Every action is recorded with a UTC timestamp for review.         ║
║  • The module uses OS-level (SendInput / DirectInput) APIs via       ║
║    pydirectinput so that inputs are visible to EAC-protected games.  ║
╚══════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from .skill_library import Skill

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pydirectinput is Windows-only.  On other platforms we log a warning and
# fall back to a stub so the rest of the orchestrator can still import
# this module without crashing.
# ---------------------------------------------------------------------------
try:
    import pydirectinput  # type: ignore[import-untyped]

    pydirectinput.PAUSE = 0.0  # we manage our own timing
    _HAS_DIRECTINPUT = True
except Exception:
    pydirectinput = None  # type: ignore[assignment]
    _HAS_DIRECTINPUT = False
    logger.warning(
        "pydirectinput unavailable — input simulation will be non-functional. "
        "Install pydirectinput on Windows to enable real input injection."
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InputSimulator:
    """EAC-safe keystroke and mouse simulator.

    **Disabled by default.**  When ``enabled`` is ``False`` every public
    method is a no-op that returns ``False``.  Every attempted action —
    successful or not — is appended to :pyattr:`action_log` for auditing.
    """

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._action_log: list[dict[str, Any]] = []
        if enabled and not _HAS_DIRECTINPUT:
            logger.warning(
                "InputSimulator created with enabled=True but "
                "pydirectinput is not available — inputs will fail."
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether the simulator is allowed to send inputs."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        logger.info("InputSimulator enabled=%s", value)

    @property
    def action_log(self) -> list[dict[str, Any]]:
        """Full audit trail of every action attempted."""
        return list(self._action_log)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record(
        self,
        action: str,
        params: dict[str, Any],
        success: bool,
    ) -> None:
        self._action_log.append({
            "timestamp": _now_iso(),
            "action": action,
            "params": params,
            "success": success,
        })

    def _check_enabled(self, action: str, params: dict[str, Any]) -> bool:
        """Return True if we may proceed.  Logs and records a no-op otherwise."""
        if not self._enabled:
            logger.debug(
                "InputSimulator disabled — skipping %s(%s)", action, params
            )
            self._record(action, params, success=False)
            return False
        if not _HAS_DIRECTINPUT:
            logger.warning(
                "pydirectinput not available — cannot execute %s", action
            )
            self._record(action, params, success=False)
            return False
        return True

    async def _run_blocking(self, func: Any, *args: Any) -> None:
        """Run a synchronous pydirectinput call in the default executor."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, func, *args)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    async def press_key(self, key: str, duration: float = 0.05) -> bool:
        """Press and release *key* with a brief hold.

        Parameters
        ----------
        key:
            Key name recognised by pydirectinput (e.g. ``"w"``, ``"space"``).
        duration:
            Seconds to hold the key before releasing.
        """
        params: dict[str, Any] = {"key": key, "duration": duration}
        if not self._check_enabled("press_key", params):
            return False

        try:
            await self._run_blocking(pydirectinput.keyDown, key)
            await asyncio.sleep(duration)
            await self._run_blocking(pydirectinput.keyUp, key)
            logger.debug("press_key %s (%.3fs)", key, duration)
            self._record("press_key", params, success=True)
            return True
        except Exception:
            logger.error("press_key failed for %r", key, exc_info=True)
            self._record("press_key", params, success=False)
            return False

    async def hold_key(self, key: str, duration: float = 1.0) -> bool:
        """Hold *key* down for *duration* seconds, then release.

        Useful for sustained inputs like throttle or afterburner.
        """
        params: dict[str, Any] = {"key": key, "duration": duration}
        if not self._check_enabled("hold_key", params):
            return False

        try:
            await self._run_blocking(pydirectinput.keyDown, key)
            await asyncio.sleep(duration)
            await self._run_blocking(pydirectinput.keyUp, key)
            logger.debug("hold_key %s (%.3fs)", key, duration)
            self._record("hold_key", params, success=True)
            return True
        except Exception:
            logger.error("hold_key failed for %r", key, exc_info=True)
            self._record("hold_key", params, success=False)
            return False

    async def release_key(self, key: str) -> bool:
        """Explicitly release *key* (safety valve for stuck keys)."""
        params: dict[str, Any] = {"key": key}
        if not self._check_enabled("release_key", params):
            return False

        try:
            await self._run_blocking(pydirectinput.keyUp, key)
            logger.debug("release_key %s", key)
            self._record("release_key", params, success=True)
            return True
        except Exception:
            logger.error("release_key failed for %r", key, exc_info=True)
            self._record("release_key", params, success=False)
            return False

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    async def mouse_click(self, button: str = "left") -> bool:
        """Click a mouse button.

        Parameters
        ----------
        button:
            ``"left"``, ``"right"``, or ``"middle"``.
        """
        params: dict[str, Any] = {"button": button}
        if not self._check_enabled("mouse_click", params):
            return False

        try:
            await self._run_blocking(pydirectinput.click, button=button)
            logger.debug("mouse_click %s", button)
            self._record("mouse_click", params, success=True)
            return True
        except Exception:
            logger.error(
                "mouse_click failed for %r", button, exc_info=True
            )
            self._record("mouse_click", params, success=False)
            return False

    async def mouse_move(self, x: int, y: int) -> bool:
        """Move the mouse cursor to absolute screen coordinates (*x*, *y*)."""
        params: dict[str, Any] = {"x": x, "y": y}
        if not self._check_enabled("mouse_move", params):
            return False

        try:
            await self._run_blocking(pydirectinput.moveTo, x, y)
            logger.debug("mouse_move (%d, %d)", x, y)
            self._record("mouse_move", params, success=True)
            return True
        except Exception:
            logger.error(
                "mouse_move failed for (%d, %d)", x, y, exc_info=True
            )
            self._record("mouse_move", params, success=False)
            return False

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    async def execute_skill(self, skill: Skill) -> bool:
        """Execute every step in *skill* sequentially, respecting timing.

        Each :class:`~.skill_library.SkillStep` maps to one of the atomic
        input methods above.  If any step fails the remaining steps are
        skipped and the method returns ``False``.
        """
        params: dict[str, Any] = {
            "skill_name": skill.name,
            "step_count": len(skill.steps),
        }
        if not self._check_enabled("execute_skill", params):
            return False

        logger.info(
            "Executing skill %r (%d steps)", skill.name, len(skill.steps)
        )

        for idx, step in enumerate(skill.steps):
            ok = await self._dispatch_step(step.action, step.parameters)
            if not ok:
                logger.warning(
                    "Skill %r failed at step %d/%d (%s)",
                    skill.name,
                    idx + 1,
                    len(skill.steps),
                    step.action,
                )
                self._record("execute_skill", params, success=False)
                return False

            if step.wait_after_ms > 0:
                await asyncio.sleep(step.wait_after_ms / 1000.0)

        logger.info("Skill %r completed successfully", skill.name)
        self._record("execute_skill", params, success=True)
        return True

    async def _dispatch_step(
        self, action: str, parameters: dict[str, Any]
    ) -> bool:
        """Route a single skill step to the matching input method."""
        if action == "press_key":
            return await self.press_key(
                key=parameters["key"],
                duration=parameters.get("duration", 0.05),
            )
        if action == "hold_key":
            return await self.hold_key(
                key=parameters["key"],
                duration=parameters.get("duration", 1.0),
            )
        if action == "release_key":
            return await self.release_key(key=parameters["key"])
        if action == "mouse_click":
            return await self.mouse_click(
                button=parameters.get("button", "left"),
            )
        if action == "mouse_move":
            return await self.mouse_move(
                x=parameters["x"],
                y=parameters["y"],
            )
        if action == "wait":
            duration = parameters.get("duration", 0.0)
            await asyncio.sleep(duration)
            return True

        logger.warning("Unknown skill step action: %r", action)
        return False
