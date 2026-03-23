"""Game activity detection for Star Citizen -- replaces MERLIN's FlightPhaseDetector.

Evaluates the composite GameState to determine what the player is currently
doing. Uses a hysteresis pattern (consecutive-detection gating) to prevent
rapid activity oscillation from noisy inputs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .game_state import GameActivity, GameState
from .log_patterns import LogEventType

logger = logging.getLogger(__name__)

# Log event types that indicate active combat.
_COMBAT_EVENT_TYPES: frozenset[str] = frozenset({
    LogEventType.PLAYER_KILL,
    LogEventType.PLAYER_DEATH,
    LogEventType.VEHICLE_DESTROYED,
})

# Log event types that indicate quantum travel.
_QT_EVENT_TYPES: frozenset[str] = frozenset({
    LogEventType.QUANTUM_TRAVEL_START,
})

# Speed threshold (SCM units) below which a ship is considered idle.
_SHIP_IDLE_SPEED: float = 5.0


@dataclass
class ActivityThresholds:
    """Configurable thresholds for activity detection."""

    combat_log_recency_secs: float = 10.0
    """How recent a combat log event must be to trigger COMBAT."""

    qt_log_recency_secs: float = 5.0
    """Quantum travel event recency window."""

    hysteresis_count: int = 3
    """Consecutive identical detections required before activity transition."""


class GameActivityDetector:
    """Analyzes GameState to determine the current player activity.

    Mirrors MERLIN's FlightPhaseDetector architecture: a raw detection
    function proposes a candidate activity, and a hysteresis gate ensures
    the candidate is seen N consecutive times before the confirmed activity
    changes.  This prevents flicker from noisy vision / log data.
    """

    def __init__(self, thresholds: ActivityThresholds | None = None) -> None:
        self._thresholds = thresholds or ActivityThresholds()
        self._current_activity: GameActivity = GameActivity.IDLE
        self._previous_activity: GameActivity = GameActivity.IDLE
        self._pending_activity: GameActivity | None = None
        self._consecutive_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_activity(self) -> GameActivity:
        """Return the current confirmed activity."""
        return self._current_activity

    @property
    def previous_activity(self) -> GameActivity:
        """Return the activity before the last transition (useful for edge triggers)."""
        return self._previous_activity

    def update(self, state: GameState) -> GameActivity:
        """Evaluate *state* and return the confirmed activity.

        The returned value only changes after the same candidate activity
        has been detected for ``thresholds.hysteresis_count`` consecutive
        calls.
        """
        candidate = self._detect_activity(state)

        if candidate != self._current_activity:
            if candidate == self._pending_activity:
                self._consecutive_count += 1
            else:
                self._pending_activity = candidate
                self._consecutive_count = 1

            if self._consecutive_count >= self._thresholds.hysteresis_count:
                self._previous_activity = self._current_activity
                self._current_activity = candidate
                self._pending_activity = None
                self._consecutive_count = 0
                logger.info(
                    "Activity: %s -> %s",
                    self._previous_activity.value,
                    self._current_activity.value,
                )
        else:
            # Current activity is still being detected -- reset pending.
            self._pending_activity = None
            self._consecutive_count = 0

        return self._current_activity

    # ------------------------------------------------------------------
    # Core detection logic
    # ------------------------------------------------------------------

    def _detect_activity(self, state: GameState) -> GameActivity:
        """Return the *instantaneous* best-guess activity from raw state.

        Checks are ordered by priority -- combat signals trump everything,
        followed by quantum travel, then gameplay loops (mining, salvage,
        trading), then generic ship/on-foot states.
        """
        now = time.time()

        # --- Log-event driven detections (highest signal) ----------------
        if self._has_recent_event(state.raw_log_events, _COMBAT_EVENT_TYPES, now,
                                  self._thresholds.combat_log_recency_secs):
            return GameActivity.COMBAT

        if self._has_recent_event(state.raw_log_events, _QT_EVENT_TYPES, now,
                                  self._thresholds.qt_log_recency_secs):
            return GameActivity.QUANTUM_TRAVEL

        # --- Ship telemetry driven detections ----------------------------
        if state.ship.quantum_drive_active:
            return GameActivity.QUANTUM_TRAVEL

        if state.combat.under_attack:
            return GameActivity.COMBAT

        if state.ship.weapons_armed and state.combat.hostile_count > 0:
            return GameActivity.COMBAT

        # --- Vision-data driven gameplay loops ---------------------------
        vision = state.vision_data

        if _vision_flag(vision, "mining_laser_active"):
            return GameActivity.MINING

        if _vision_flag(vision, "salvage_active"):
            return GameActivity.SALVAGE

        if _vision_flag(vision, "trade_terminal"):
            return GameActivity.TRADING

        # --- Generic ship / on-foot fallback -----------------------------
        if state.player.in_ship:
            if state.ship.speed_scm > _SHIP_IDLE_SPEED:
                return GameActivity.SHIP_FLIGHT
            return GameActivity.SHIP_IDLE

        # Not in a ship -- player is on foot (or idle at a menu, etc.)
        if state.player.in_vehicle:
            return GameActivity.SHIP_FLIGHT  # ground vehicles share this for now

        return GameActivity.ON_FOOT if state.timestamp else GameActivity.IDLE

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_recent_event(
        events: list[dict[str, Any]],
        event_types: frozenset[str],
        now: float,
        recency_secs: float,
    ) -> bool:
        """Return True if *events* contains a matching event within the recency window.

        Each event dict is expected to carry at least ``"type"`` (a
        ``LogEventType`` value or equivalent string) and ``"epoch"``
        (a Unix timestamp float).  Events lacking these keys are
        silently skipped.
        """
        cutoff = now - recency_secs
        for evt in events:
            evt_type = evt.get("type", "")
            evt_epoch = evt.get("epoch")
            if evt_epoch is None:
                continue
            try:
                if float(evt_epoch) >= cutoff and evt_type in event_types:
                    return True
            except (TypeError, ValueError):
                continue
        return False


def _vision_flag(vision: dict[str, Any], key: str) -> bool:
    """Safely read a boolean-ish flag from the vision_data dict."""
    val = vision.get(key)
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "active")
    return False
