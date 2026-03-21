"""Flight phase detection from simulator telemetry."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .sim_client import FlightPhase, SimState

logger = logging.getLogger(__name__)


@dataclass
class PhaseThresholds:
    taxi_ground_speed: float = 5.0  # knots - moving on ground
    takeoff_speed: float = 40.0  # knots - committed to takeoff roll
    airborne_agl: float = 50.0  # feet - definitely airborne
    climb_vs: float = 300.0  # fpm - in a climb
    cruise_vs_band: float = 200.0  # fpm - level flight band
    descent_vs: float = -300.0  # fpm - in a descent
    approach_agl: float = 3000.0  # feet - in terminal area
    landing_agl: float = 200.0  # feet - on short final
    landed_ground_speed: float = 60.0  # knots - slowing after touchdown


class FlightPhaseDetector:
    """Analyzes SimState telemetry to determine the current flight phase.

    Uses a state-machine approach with hysteresis to prevent rapid phase
    oscillation. Each phase transition requires sustained conditions over
    a short stabilization window.
    """

    def __init__(self, thresholds: PhaseThresholds | None = None) -> None:
        self._thresholds = thresholds or PhaseThresholds()
        self._current_phase = FlightPhase.PREFLIGHT
        self._phase_hold_count: int = 0
        self._hold_required: int = 3  # consecutive detections before transition

    @property
    def current_phase(self) -> FlightPhase:
        return self._current_phase

    def update(self, state: SimState) -> FlightPhase:
        """Evaluate telemetry and return the detected flight phase."""
        candidate = self._detect_phase(state)

        if candidate != self._current_phase:
            self._phase_hold_count += 1
            if self._phase_hold_count >= self._hold_required:
                previous = self._current_phase
                self._current_phase = candidate
                self._phase_hold_count = 0
                logger.info("Flight phase: %s -> %s", previous.value, candidate.value)
        else:
            self._phase_hold_count = 0

        return self._current_phase

    def _detect_phase(self, s: SimState) -> FlightPhase:
        t = self._thresholds
        on_ground = s.on_ground
        gs = s.speeds.ground_speed
        vs = s.speeds.vertical_speed
        agl = s.position.altitude_agl
        gear = s.surfaces.gear_handle
        flaps = s.surfaces.flaps_percent
        has_power = any(e.rpm > 100 for e in s.engines.active_engines)

        # On the ground
        if on_ground:
            # After landing, stay LANDED until speed drops to taxi
            if self._current_phase in (FlightPhase.LANDING, FlightPhase.LANDED):
                if gs < t.taxi_ground_speed and not has_power:
                    return FlightPhase.PREFLIGHT
                return FlightPhase.LANDED
            if gs < t.taxi_ground_speed:
                if not has_power and self._current_phase == FlightPhase.PREFLIGHT:
                    return FlightPhase.PREFLIGHT
                return FlightPhase.PREFLIGHT if not has_power else FlightPhase.TAXI
            if gs >= t.takeoff_speed:
                return FlightPhase.TAKEOFF
            return FlightPhase.TAXI

        # Airborne — check climb first (handles go-arounds from approach)
        if vs >= t.climb_vs:
            return FlightPhase.CLIMB

        if agl < t.landing_agl and gear and vs < 0:
            return FlightPhase.LANDING

        if agl < t.approach_agl and gear:
            if vs <= t.descent_vs:
                return FlightPhase.APPROACH
            if flaps > 0:
                return FlightPhase.APPROACH
            return FlightPhase.DESCENT

        if vs <= t.descent_vs:
            return FlightPhase.DESCENT

        if abs(vs) <= t.cruise_vs_band:
            return FlightPhase.CRUISE

        # Default: maintain current phase
        return self._current_phase
