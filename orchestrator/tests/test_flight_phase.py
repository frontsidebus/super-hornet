"""Tests for orchestrator.flight_phase — FlightPhaseDetector state machine.

This is the most critical test file. It exercises the phase detection logic
with realistic telemetry sequences and verifies hysteresis behaviour.
"""

from __future__ import annotations

import pytest

from orchestrator.flight_phase import FlightPhaseDetector, PhaseThresholds
from orchestrator.sim_client import (
    Attitude,
    EngineParams,
    Environment,
    FlightPhase,
    Position,
    SimState,
    Speeds,
    SurfaceState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(
    *,
    on_ground: bool = True,
    ground_speed: float = 0,
    indicated: float = 0,
    vertical_speed: float = 0,
    altitude_agl: float = 0,
    gear_down: bool = True,
    flaps_position: int = 0,
    rpm: list[float] | None = None,
    n1: list[float] | None = None,
) -> SimState:
    """Shorthand factory for building a SimState focused on phase-relevant params."""
    return SimState(
        on_ground=on_ground,
        position=Position(altitude_agl=altitude_agl, altitude=altitude_agl + 100),
        speeds=Speeds(
            indicated=indicated,
            true_airspeed=indicated,
            ground_speed=ground_speed,
            vertical_speed=vertical_speed,
        ),
        surfaces=SurfaceState(
            gear_down=gear_down,
            gear_retractable=True,
            flaps_position=flaps_position,
            flaps_num_positions=4,
        ),
        engine=EngineParams(
            rpm=rpm if rpm is not None else [0],
            n1=n1 if n1 is not None else [],
        ),
    )


def _repeat_update(detector: FlightPhaseDetector, state: SimState, n: int = 5) -> FlightPhase:
    """Call detector.update n times with the same state and return the final phase."""
    phase = FlightPhase.PREFLIGHT
    for _ in range(n):
        phase = detector.update(state)
    return phase


# ---------------------------------------------------------------------------
# Phase detection — individual scenarios
# ---------------------------------------------------------------------------


class TestPreflightDetection:
    """Parked on the ramp with no power."""

    def test_engines_off_on_ground_is_preflight(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=0, rpm=[0])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.PREFLIGHT

    def test_engines_off_zero_speed_stays_preflight(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=0, rpm=[0])
        for _ in range(10):
            d.update(s)
        assert d.current_phase == FlightPhase.PREFLIGHT

    def test_engines_just_started_still_stationary_becomes_taxi(self) -> None:
        """Engine running, stationary, should become TAXI (has power, low speed)."""
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=0, rpm=[800])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAXI


class TestTaxiDetection:
    """Moving on the ground at low speed with engines running."""

    def test_low_ground_speed_with_power(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=12, rpm=[1800])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAXI

    def test_very_slow_taxi(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=6, rpm=[1200])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAXI

    def test_stationary_with_power_is_taxi_not_preflight(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=3, rpm=[1800])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAXI


class TestTakeoffDetection:
    """Accelerating on the runway above takeoff speed threshold."""

    def test_high_ground_speed_on_ground(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=55, rpm=[2700])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAKEOFF

    def test_at_takeoff_speed_threshold(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=40, rpm=[2700])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAKEOFF

    def test_below_takeoff_speed_is_taxi(self) -> None:
        d = FlightPhaseDetector()
        s = _state(on_ground=True, ground_speed=35, rpm=[2700])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAXI


class TestClimbDetection:
    """Airborne with positive vertical speed above threshold."""

    def test_strong_climb_airborne(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=5000, vertical_speed=800,
            indicated=85, gear_down=False, rpm=[2700],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CLIMB

    def test_marginal_climb_at_threshold(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=5000, vertical_speed=301,
            indicated=85, gear_down=False, rpm=[2700],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CLIMB

    def test_below_climb_threshold_not_climb(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=5000, vertical_speed=150,
            indicated=120, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        # 150 fpm is within cruise VS band (±200)
        assert phase == FlightPhase.CRUISE


class TestCruiseDetection:
    """Airborne with stable (near-zero) vertical speed at altitude."""

    def test_level_flight_high_altitude(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=8000, vertical_speed=50,
            indicated=130, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CRUISE

    def test_exactly_zero_vs(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=6500, vertical_speed=0,
            indicated=120, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CRUISE

    def test_slight_positive_vs_still_cruise(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=6500, vertical_speed=199,
            indicated=120, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CRUISE

    def test_slight_negative_vs_still_cruise(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=6500, vertical_speed=-199,
            indicated=120, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CRUISE


class TestDescentDetection:
    """Airborne with negative vertical speed below threshold, above approach altitude."""

    def test_strong_descent_high_altitude(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=8000, vertical_speed=-600,
            indicated=130, gear_down=False, rpm=[2200],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.DESCENT

    def test_descent_at_threshold(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=8000, vertical_speed=-301,
            indicated=130, gear_down=False, rpm=[2200],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.DESCENT

    def test_descent_low_alt_gear_up_no_flaps(self) -> None:
        """Below approach AGL but gear up -> still DESCENT, not APPROACH."""
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=2500, vertical_speed=-500,
            indicated=130, gear_down=False, flaps_position=0, rpm=[2200],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.DESCENT


class TestApproachDetection:
    """Low altitude, gear down, descending or with flaps."""

    def test_low_alt_gear_down_descending(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=2000, vertical_speed=-500,
            indicated=90, gear_down=True, flaps_position=0, rpm=[2100],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.APPROACH

    def test_low_alt_gear_down_with_flaps(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=2500, vertical_speed=-100,
            indicated=90, gear_down=True, flaps_position=2, rpm=[2100],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.APPROACH

    def test_low_alt_gear_down_level_no_flaps_is_descent(self) -> None:
        """Gear down, low alt, but VS not below descent threshold and no flaps => DESCENT."""
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=2500, vertical_speed=-100,
            indicated=100, gear_down=True, flaps_position=0, rpm=[2200],
        )
        phase = _repeat_update(d, s)
        # VS=-100 is not < -300, flaps=0 => else branch => DESCENT
        assert phase == FlightPhase.DESCENT

    def test_above_approach_agl_is_not_approach(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=3500, vertical_speed=-500,
            indicated=90, gear_down=True, flaps_position=2, rpm=[2100],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.DESCENT


class TestLandingDetection:
    """Very low altitude, gear down, negative VS — on short final."""

    def test_short_final(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=100, vertical_speed=-300,
            indicated=65, gear_down=True, flaps_position=3, rpm=[1500],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.LANDING

    def test_at_landing_agl_threshold(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=199, vertical_speed=-200,
            indicated=65, gear_down=True, flaps_position=3, rpm=[1500],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.LANDING

    def test_low_alt_gear_up_is_not_landing(self) -> None:
        """Even at low AGL, gear up prevents LANDING detection."""
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=100, vertical_speed=-300,
            indicated=65, gear_down=False, rpm=[1500],
        )
        phase = _repeat_update(d, s)
        # Gear up, low alt => won't match landing or approach gear checks
        # VS < descent_vs => DESCENT
        assert phase == FlightPhase.DESCENT


class TestLandedDetection:
    """On the ground after touchdown, decelerating."""

    def test_touchdown_transition(self) -> None:
        d = FlightPhaseDetector()
        # First establish LANDING
        landing = _state(
            on_ground=False, altitude_agl=50, vertical_speed=-300,
            indicated=65, gear_down=True, flaps_position=3, rpm=[1500],
        )
        _repeat_update(d, landing)
        assert d.current_phase == FlightPhase.LANDING

        # Now on the ground, decelerating
        on_ground = _state(
            on_ground=True, ground_speed=40, vertical_speed=0,
            indicated=45, gear_down=True, flaps_position=3, rpm=[1200],
        )
        phase = _repeat_update(d, on_ground)
        assert phase == FlightPhase.LANDED

    def test_slow_taxi_after_landing_stays_landed(self) -> None:
        d = FlightPhaseDetector()
        # Manually set current phase to LANDING
        d._current_phase = FlightPhase.LANDING
        s = _state(
            on_ground=True, ground_speed=3, vertical_speed=0,
            rpm=[800], gear_down=True,
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.LANDED


# ---------------------------------------------------------------------------
# Hysteresis / stabilization
# ---------------------------------------------------------------------------


class TestHysteresis:
    """The detector requires _hold_required consecutive detections before transitioning."""

    def test_single_detection_does_not_transition(self) -> None:
        d = FlightPhaseDetector()
        assert d.current_phase == FlightPhase.PREFLIGHT

        taxi_state = _state(on_ground=True, ground_speed=12, rpm=[1800])
        d.update(taxi_state)
        # Only 1 consecutive detection -- should NOT have transitioned
        assert d.current_phase == FlightPhase.PREFLIGHT

    def test_two_detections_below_threshold(self) -> None:
        d = FlightPhaseDetector()
        taxi_state = _state(on_ground=True, ground_speed=12, rpm=[1800])
        d.update(taxi_state)
        d.update(taxi_state)
        # 2 < 3 (default hold required) -- still PREFLIGHT
        assert d.current_phase == FlightPhase.PREFLIGHT

    def test_three_detections_triggers_transition(self) -> None:
        d = FlightPhaseDetector()
        taxi_state = _state(on_ground=True, ground_speed=12, rpm=[1800])
        d.update(taxi_state)
        d.update(taxi_state)
        d.update(taxi_state)
        assert d.current_phase == FlightPhase.TAXI

    def test_interrupted_sequence_resets_counter(self) -> None:
        d = FlightPhaseDetector()
        taxi_state = _state(on_ground=True, ground_speed=12, rpm=[1800])
        preflight_state = _state(on_ground=True, ground_speed=0, rpm=[0])

        d.update(taxi_state)
        d.update(taxi_state)
        # Now interrupt with the current phase state
        d.update(preflight_state)
        # Counter should have been reset
        d.update(taxi_state)
        d.update(taxi_state)
        # Still only 2 consecutive -- should not have transitioned
        assert d.current_phase == FlightPhase.PREFLIGHT

    def test_custom_hold_required(self) -> None:
        d = FlightPhaseDetector()
        d._hold_required = 1  # Transition immediately
        taxi_state = _state(on_ground=True, ground_speed=12, rpm=[1800])
        d.update(taxi_state)
        assert d.current_phase == FlightPhase.TAXI


class TestCustomThresholds:
    """Verify that custom PhaseThresholds override the defaults."""

    def test_higher_taxi_threshold(self) -> None:
        thresholds = PhaseThresholds(taxi_ground_speed=15.0)
        d = FlightPhaseDetector(thresholds=thresholds)
        # 12 kts is below new threshold of 15 -- still "slow on ground"
        s = _state(on_ground=True, ground_speed=12, rpm=[1800])
        phase = _repeat_update(d, s)
        # 12 < 15 and has_power => TAXI (it's still in the low-speed branch)
        assert phase == FlightPhase.TAXI

    def test_lower_takeoff_speed(self) -> None:
        thresholds = PhaseThresholds(takeoff_speed=30.0)
        d = FlightPhaseDetector(thresholds=thresholds)
        s = _state(on_ground=True, ground_speed=35, rpm=[2700])
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.TAKEOFF

    def test_higher_approach_agl(self) -> None:
        thresholds = PhaseThresholds(approach_agl=5000.0)
        d = FlightPhaseDetector(thresholds=thresholds)
        s = _state(
            on_ground=False, altitude_agl=4000, vertical_speed=-500,
            gear_down=True, rpm=[2100],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.APPROACH


# ---------------------------------------------------------------------------
# Full flight sequences
# ---------------------------------------------------------------------------


class TestFullFlightSequence:
    """Simulate a complete flight from preflight through landing."""

    def test_normal_vfr_flight(self) -> None:
        d = FlightPhaseDetector()
        d._hold_required = 1  # Skip hysteresis for cleaner sequencing

        # Preflight - engines off, parked
        phase = d.update(_state(on_ground=True, ground_speed=0, rpm=[0]))
        assert phase == FlightPhase.PREFLIGHT

        # Engine start, begin taxi
        phase = d.update(_state(on_ground=True, ground_speed=10, rpm=[1800]))
        assert phase == FlightPhase.TAXI

        # Takeoff roll
        phase = d.update(_state(on_ground=True, ground_speed=55, rpm=[2700]))
        assert phase == FlightPhase.TAKEOFF

        # Initial climb
        phase = d.update(_state(
            on_ground=False, altitude_agl=500, vertical_speed=800,
            indicated=85, gear_down=False, rpm=[2700],
        ))
        assert phase == FlightPhase.CLIMB

        # Level off at cruise
        phase = d.update(_state(
            on_ground=False, altitude_agl=6500, vertical_speed=50,
            indicated=120, gear_down=False, rpm=[2400],
        ))
        assert phase == FlightPhase.CRUISE

        # Begin descent
        phase = d.update(_state(
            on_ground=False, altitude_agl=5000, vertical_speed=-600,
            indicated=130, gear_down=False, rpm=[2200],
        ))
        assert phase == FlightPhase.DESCENT

        # Approach — gear down, flaps, below 3000 AGL
        phase = d.update(_state(
            on_ground=False, altitude_agl=2000, vertical_speed=-500,
            indicated=90, gear_down=True, flaps_position=2, rpm=[2100],
        ))
        assert phase == FlightPhase.APPROACH

        # Short final — below 200 AGL
        phase = d.update(_state(
            on_ground=False, altitude_agl=100, vertical_speed=-300,
            indicated=65, gear_down=True, flaps_position=3, rpm=[1500],
        ))
        assert phase == FlightPhase.LANDING

        # Touchdown and deceleration
        phase = d.update(_state(
            on_ground=True, ground_speed=40, vertical_speed=0,
            indicated=45, gear_down=True, flaps_position=3, rpm=[1200],
        ))
        assert phase == FlightPhase.LANDED

    def test_jet_with_n1(self) -> None:
        """Ensure has_power check works with N1 (jet engines) instead of RPM."""
        d = FlightPhaseDetector()
        d._hold_required = 1

        # Parked with engines off
        phase = d.update(_state(on_ground=True, ground_speed=0, rpm=[0], n1=[0]))
        assert phase == FlightPhase.PREFLIGHT

        # Engines started (N1 > 5)
        phase = d.update(_state(on_ground=True, ground_speed=0, rpm=[0], n1=[22.0]))
        assert phase == FlightPhase.TAXI

    def test_go_around_from_approach(self) -> None:
        """Go-around: transition from APPROACH back to CLIMB."""
        d = FlightPhaseDetector()
        d._hold_required = 1

        # On approach
        d.update(_state(
            on_ground=False, altitude_agl=800, vertical_speed=-500,
            indicated=90, gear_down=True, flaps_position=2, rpm=[2100],
        ))

        # Go around -- full power, positive VS, climbing
        phase = d.update(_state(
            on_ground=False, altitude_agl=900, vertical_speed=1000,
            indicated=80, gear_down=True, flaps_position=1, rpm=[2700],
        ))
        assert phase == FlightPhase.CLIMB


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Exercise boundary conditions and unusual telemetry."""

    def test_zero_vs_airborne_high_alt_is_cruise(self) -> None:
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=10000, vertical_speed=0,
            indicated=150, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CRUISE

    def test_negative_vs_at_200_boundary(self) -> None:
        """VS exactly at cruise band boundary."""
        d = FlightPhaseDetector()
        s = _state(
            on_ground=False, altitude_agl=8000, vertical_speed=-200,
            indicated=130, gear_down=False, rpm=[2400],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.CRUISE

    def test_exactly_at_approach_agl_boundary(self) -> None:
        d = FlightPhaseDetector()
        # altitude_agl=3000 is NOT < 3000 so approach check fails => climb/descent/cruise
        s = _state(
            on_ground=False, altitude_agl=3000, vertical_speed=-500,
            indicated=130, gear_down=True, flaps_position=2, rpm=[2100],
        )
        phase = _repeat_update(d, s)
        assert phase == FlightPhase.DESCENT

    def test_sim_paused_preserves_phase(self) -> None:
        """Pausing shouldn't change detected phase — update just gets the same state."""
        d = FlightPhaseDetector()
        cruise = _state(
            on_ground=False, altitude_agl=6500, vertical_speed=0,
            indicated=120, gear_down=False, rpm=[2400],
        )
        _repeat_update(d, cruise)
        assert d.current_phase == FlightPhase.CRUISE

        paused = cruise.model_copy(update={"sim_paused": True})
        phase = d.update(paused)
        assert phase == FlightPhase.CRUISE
