"""Shared fixtures for MERLIN orchestrator test suite."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.sim_client import (
    Attitude,
    AutopilotState,
    EngineParams,
    Environment,
    FlightPhase,
    FuelState,
    Position,
    RadioState,
    SimState,
    SurfaceState,
    Speeds,
)


# ---------------------------------------------------------------------------
# Configuration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set minimal environment variables for Settings to load without a .env file."""
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-test-key-000",
        "ELEVENLABS_API_KEY": "el-test-key",
        "SIMCONNECT_BRIDGE_URL": "ws://localhost:9999",
        "WHISPER_MODEL": "tiny",
        "WHISPER_URL": "http://localhost:9000",
        "VOICE_ID": "test-voice-id",
        "SCREEN_CAPTURE_ENABLED": "false",
        "SCREEN_CAPTURE_FPS": "2",
        "CLAUDE_MODEL": "claude-sonnet-4-20250514",
        "CHROMADB_PATH": "/tmp/test_chromadb",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


# ---------------------------------------------------------------------------
# SimState factory helpers
# ---------------------------------------------------------------------------


def _make_sim_state(**overrides: Any) -> SimState:
    """Build a SimState with sensible defaults, applying overrides."""
    defaults: dict[str, Any] = {
        "timestamp": 1000.0,
        "aircraft_title": "Cessna 172 Skyhawk",
        "on_ground": True,
        "sim_paused": False,
        "flight_phase": FlightPhase.PREFLIGHT,
    }
    defaults.update(overrides)
    return SimState(**defaults)


@pytest.fixture
def sim_state_parked() -> SimState:
    """Aircraft parked on the ramp, engines off."""
    return _make_sim_state(
        position=Position(latitude=28.4294, longitude=-81.309, altitude=96, altitude_agl=0),
        attitude=Attitude(pitch=0, bank=0, heading=270),
        speeds=Speeds(indicated=0, true_airspeed=0, ground_speed=0, mach=0, vertical_speed=0),
        engine=EngineParams(rpm=[0], fuel_flow=[0], oil_temp=[75], oil_pressure=[0]),
        surfaces=SurfaceState(gear_down=True, gear_retractable=False, flaps_position=0, parking_brake=True),
        fuel=FuelState(quantities=[21, 21], total=42, total_weight=252),
        environment=Environment(wind_speed=5, wind_direction=270, visibility=10, temperature=25, pressure=29.92),
        on_ground=True,
        flight_phase=FlightPhase.PREFLIGHT,
    )


@pytest.fixture
def sim_state_taxiing() -> SimState:
    """Aircraft taxiing at low speed with engines running."""
    return _make_sim_state(
        position=Position(latitude=28.4294, longitude=-81.309, altitude=96, altitude_agl=0),
        attitude=Attitude(pitch=0, bank=0, heading=90),
        speeds=Speeds(indicated=12, true_airspeed=12, ground_speed=12, mach=0, vertical_speed=0),
        engine=EngineParams(rpm=[1800], fuel_flow=[8], oil_temp=[180], oil_pressure=[60]),
        surfaces=SurfaceState(gear_down=True, flaps_position=0, parking_brake=False),
        on_ground=True,
        flight_phase=FlightPhase.TAXI,
    )


@pytest.fixture
def sim_state_takeoff_roll() -> SimState:
    """Aircraft accelerating on the runway for takeoff."""
    return _make_sim_state(
        position=Position(latitude=28.4294, longitude=-81.309, altitude=96, altitude_agl=0),
        attitude=Attitude(pitch=0, bank=0, heading=90),
        speeds=Speeds(indicated=55, true_airspeed=55, ground_speed=55, mach=0.08, vertical_speed=0),
        engine=EngineParams(rpm=[2700], fuel_flow=[14], oil_temp=[200], oil_pressure=[65]),
        surfaces=SurfaceState(gear_down=True, flaps_position=1, flaps_num_positions=4),
        on_ground=True,
        flight_phase=FlightPhase.TAKEOFF,
    )


@pytest.fixture
def sim_state_initial_climb() -> SimState:
    """Aircraft just after liftoff, climbing through 500 ft AGL."""
    return _make_sim_state(
        position=Position(latitude=28.43, longitude=-81.30, altitude=600, altitude_agl=500),
        attitude=Attitude(pitch=10, bank=0, heading=90),
        speeds=Speeds(indicated=85, true_airspeed=86, ground_speed=82, mach=0.13, vertical_speed=800),
        engine=EngineParams(rpm=[2700], fuel_flow=[14], oil_temp=[200], oil_pressure=[65]),
        surfaces=SurfaceState(gear_down=False, gear_retractable=True, flaps_position=1, flaps_num_positions=4),
        on_ground=False,
        flight_phase=FlightPhase.CLIMB,
    )


@pytest.fixture
def sim_state_cruise() -> SimState:
    """Aircraft in stable cruise at FL065."""
    return _make_sim_state(
        position=Position(latitude=28.6, longitude=-81.1, altitude=6500, altitude_agl=6400),
        attitude=Attitude(pitch=2, bank=0, heading=45),
        speeds=Speeds(indicated=120, true_airspeed=130, ground_speed=135, mach=0.20, vertical_speed=50),
        engine=EngineParams(rpm=[2400], fuel_flow=[9], oil_temp=[190], oil_pressure=[60]),
        autopilot=AutopilotState(master=True, heading_hold=True, altitude_hold=True, set_heading=45, set_altitude=6500),
        surfaces=SurfaceState(gear_down=False, gear_retractable=True, flaps_position=0),
        on_ground=False,
        flight_phase=FlightPhase.CRUISE,
    )


@pytest.fixture
def sim_state_descent() -> SimState:
    """Aircraft descending from cruise altitude."""
    return _make_sim_state(
        position=Position(latitude=28.7, longitude=-81.0, altitude=4500, altitude_agl=4400),
        attitude=Attitude(pitch=-3, bank=0, heading=180),
        speeds=Speeds(indicated=130, true_airspeed=138, ground_speed=140, mach=0.21, vertical_speed=-500),
        engine=EngineParams(rpm=[2200], fuel_flow=[7], oil_temp=[185], oil_pressure=[58]),
        surfaces=SurfaceState(gear_down=False, gear_retractable=True, flaps_position=0),
        on_ground=False,
        flight_phase=FlightPhase.DESCENT,
    )


@pytest.fixture
def sim_state_approach() -> SimState:
    """Aircraft on approach, gear down, some flaps."""
    return _make_sim_state(
        position=Position(latitude=28.42, longitude=-81.32, altitude=1500, altitude_agl=1400),
        attitude=Attitude(pitch=-2, bank=-5, heading=270),
        speeds=Speeds(indicated=90, true_airspeed=92, ground_speed=88, mach=0.14, vertical_speed=-500),
        engine=EngineParams(rpm=[2100], fuel_flow=[8], oil_temp=[190], oil_pressure=[60]),
        surfaces=SurfaceState(gear_down=True, gear_retractable=True, flaps_position=2, flaps_num_positions=4),
        on_ground=False,
        flight_phase=FlightPhase.APPROACH,
    )


@pytest.fixture
def sim_state_landing() -> SimState:
    """Aircraft on short final, about to touch down."""
    return _make_sim_state(
        position=Position(latitude=28.4295, longitude=-81.3095, altitude=146, altitude_agl=50),
        attitude=Attitude(pitch=3, bank=0, heading=270),
        speeds=Speeds(indicated=65, true_airspeed=66, ground_speed=62, mach=0.10, vertical_speed=-300),
        engine=EngineParams(rpm=[1500], fuel_flow=[5], oil_temp=[185], oil_pressure=[58]),
        surfaces=SurfaceState(gear_down=True, gear_retractable=True, flaps_position=3, flaps_num_positions=4),
        on_ground=False,
        flight_phase=FlightPhase.LANDING,
    )


# ---------------------------------------------------------------------------
# Sample JSON payloads
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_state_json() -> dict[str, Any]:
    """A raw JSON payload as would arrive from the SimConnect bridge."""
    return {
        "timestamp": 12345.678,
        "aircraft_title": "Cessna 172 Skyhawk",
        "position": {"latitude": 28.4294, "longitude": -81.309, "altitude": 6500, "altitude_agl": 6400},
        "attitude": {"pitch": 2, "bank": 0, "heading": 45},
        "speeds": {"indicated": 120, "true_airspeed": 130, "ground_speed": 135, "mach": 0.20, "vertical_speed": 50},
        "engine": {"rpm": [2400], "fuel_flow": [9.0], "egt": [], "oil_temp": [190], "oil_pressure": [60], "manifold_pressure": [], "n1": [], "n2": []},
        "autopilot": {"master": True, "heading_hold": True, "altitude_hold": True, "nav_hold": False, "approach_hold": False, "vertical_speed_hold": False, "set_heading": 45, "set_altitude": 6500, "set_speed": 0, "set_vertical_speed": 0},
        "radios": {"com1_active": 121.7, "com1_standby": 118.3, "com2_active": 0, "com2_standby": 0, "nav1_active": 110.3, "nav1_standby": 0, "nav2_active": 0, "nav2_standby": 0, "transponder": 1200, "adf": 0},
        "fuel": {"quantities": [21, 21], "total": 42, "total_weight": 252},
        "environment": {"wind_speed": 5, "wind_direction": 270, "visibility": 10, "temperature": 25, "pressure": 29.92, "precipitation": "none"},
        "surfaces": {"gear_down": False, "gear_retractable": True, "flaps_position": 0, "flaps_num_positions": 4, "spoilers_deployed": False, "parking_brake": False},
        "flight_phase": "CRUISE",
        "on_ground": False,
        "sim_paused": False,
    }


@pytest.fixture
def sample_state_update_message(sample_state_json: dict[str, Any]) -> str:
    """A WebSocket message wrapping a state update."""
    return json.dumps({"type": "state_update", "data": sample_state_json})


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_websocket() -> AsyncMock:
    """A mock WebSocket connection supporting send/recv/close."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"timestamp": 0}))
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def mock_chromadb_collection() -> MagicMock:
    """A mock ChromaDB collection with configurable query results."""
    coll = MagicMock()
    coll.count.return_value = 10
    coll.query.return_value = {
        "documents": [["chunk one content", "chunk two content"]],
        "metadatas": [[{"source": "poh.pdf", "chunk_index": 0}, {"source": "poh.pdf", "chunk_index": 1}]],
        "distances": [[0.12, 0.25]],
    }
    coll.upsert = MagicMock()
    return coll
