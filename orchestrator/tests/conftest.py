"""Shared fixtures for MERLIN orchestrator test suite."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.sim_client import (
    Attitude,
    AutopilotState,
    ConnectionState,
    EngineData,
    Engines,
    Environment,
    FlightPhase,
    FuelState,
    HealthMonitor,
    Position,
    RadioState,
    SimState,
    SubsystemHealth,
    SurfaceState,
    Speeds,
)


# ---------------------------------------------------------------------------
# Configuration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set minimal environment variables for Settings to load without a .env file."""
    # Prevent Settings from loading the real .env file during tests
    monkeypatch.setattr("orchestrator.config.Settings.model_config", {
        "env_file": "",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    })
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-test-key-000",
        "ELEVENLABS_API_KEY": "el-test-key",
        "ELEVENLABS_VOICE_ID": "test-voice-id",
        "SIMCONNECT_BRIDGE_URL": "ws://localhost:9999",
        "WHISPER_MODEL": "tiny",
        "WHISPER_URL": "http://localhost:9090",
        "SCREEN_CAPTURE_ENABLED": "false",
        "SCREEN_CAPTURE_FPS": "2",
        "CLAUDE_MODEL": "claude-sonnet-4-20250514",
        "CHROMADB_URL": "http://chromadb-test:9999",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


# ---------------------------------------------------------------------------
# SimState factory helpers
# ---------------------------------------------------------------------------


def _make_engine(rpm: float = 0, fuel_flow_gph: float = 0, oil_temp: float = 0,
                 oil_pressure: float = 0, manifold_pressure: float = 0, egt: float = 0) -> EngineData:
    return EngineData(
        rpm=rpm, manifold_pressure=manifold_pressure, fuel_flow_gph=fuel_flow_gph,
        egt=egt, oil_temp=oil_temp, oil_pressure=oil_pressure,
    )


def _make_sim_state(**overrides: Any) -> SimState:
    """Build a SimState with sensible defaults, applying overrides."""
    defaults: dict[str, Any] = {
        "timestamp": "2026-03-20T00:00:00+00:00",
        "connected": True,
        "aircraft": "Cessna 172 Skyhawk",
        "flight_phase": FlightPhase.PREFLIGHT,
    }
    defaults.update(overrides)
    return SimState(**defaults)


@pytest.fixture
def sim_state_parked() -> SimState:
    """Aircraft parked on the ramp, engines off."""
    return _make_sim_state(
        position=Position(latitude=28.4294, longitude=-81.309, altitude_msl=96, altitude_agl=0),
        attitude=Attitude(pitch=0, bank=0, heading_true=270, heading_magnetic=267),
        speeds=Speeds(indicated_airspeed=0, true_airspeed=0, ground_speed=0, mach=0, vertical_speed=0),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=0, fuel_flow_gph=0, oil_temp=75, oil_pressure=0)]),
        surfaces=SurfaceState(gear_handle=True, flaps_percent=0, spoilers_percent=0),
        fuel=FuelState(total_gallons=42, total_weight_lbs=252),
        environment=Environment(wind_speed_kts=5, wind_direction=270, visibility_sm=10, temperature_c=25, barometer_inhg=29.92),
        flight_phase=FlightPhase.PREFLIGHT,
    )


@pytest.fixture
def sim_state_taxiing() -> SimState:
    """Aircraft taxiing at low speed with engines running."""
    return _make_sim_state(
        position=Position(latitude=28.4294, longitude=-81.309, altitude_msl=96, altitude_agl=0),
        attitude=Attitude(pitch=0, bank=0, heading_true=90, heading_magnetic=87),
        speeds=Speeds(indicated_airspeed=12, true_airspeed=12, ground_speed=12, mach=0, vertical_speed=0),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=1800, fuel_flow_gph=8, oil_temp=180, oil_pressure=60)]),
        surfaces=SurfaceState(gear_handle=True, flaps_percent=0, spoilers_percent=0),
        flight_phase=FlightPhase.TAXI,
    )


@pytest.fixture
def sim_state_takeoff_roll() -> SimState:
    """Aircraft accelerating on the runway for takeoff."""
    return _make_sim_state(
        position=Position(latitude=28.4294, longitude=-81.309, altitude_msl=96, altitude_agl=0),
        attitude=Attitude(pitch=0, bank=0, heading_true=90, heading_magnetic=87),
        speeds=Speeds(indicated_airspeed=55, true_airspeed=55, ground_speed=55, mach=0.08, vertical_speed=0),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=2700, fuel_flow_gph=14, oil_temp=200, oil_pressure=65)]),
        surfaces=SurfaceState(gear_handle=True, flaps_percent=25, spoilers_percent=0),
        flight_phase=FlightPhase.TAKEOFF,
    )


@pytest.fixture
def sim_state_initial_climb() -> SimState:
    """Aircraft just after liftoff, climbing through 500 ft AGL."""
    return _make_sim_state(
        position=Position(latitude=28.43, longitude=-81.30, altitude_msl=600, altitude_agl=500),
        attitude=Attitude(pitch=10, bank=0, heading_true=90, heading_magnetic=87),
        speeds=Speeds(indicated_airspeed=85, true_airspeed=86, ground_speed=82, mach=0.13, vertical_speed=800),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=2700, fuel_flow_gph=14, oil_temp=200, oil_pressure=65)]),
        surfaces=SurfaceState(gear_handle=False, flaps_percent=25, spoilers_percent=0),
        flight_phase=FlightPhase.CLIMB,
    )


@pytest.fixture
def sim_state_cruise() -> SimState:
    """Aircraft in stable cruise at FL065."""
    return _make_sim_state(
        position=Position(latitude=28.6, longitude=-81.1, altitude_msl=6500, altitude_agl=6400),
        attitude=Attitude(pitch=2, bank=0, heading_true=45, heading_magnetic=42),
        speeds=Speeds(indicated_airspeed=120, true_airspeed=130, ground_speed=135, mach=0.20, vertical_speed=50),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=2400, fuel_flow_gph=9, oil_temp=190, oil_pressure=60)]),
        autopilot=AutopilotState(master=True, heading=45, altitude=6500),
        surfaces=SurfaceState(gear_handle=False, flaps_percent=0, spoilers_percent=0),
        flight_phase=FlightPhase.CRUISE,
    )


@pytest.fixture
def sim_state_descent() -> SimState:
    """Aircraft descending from cruise altitude."""
    return _make_sim_state(
        position=Position(latitude=28.7, longitude=-81.0, altitude_msl=4500, altitude_agl=4400),
        attitude=Attitude(pitch=-3, bank=0, heading_true=180, heading_magnetic=177),
        speeds=Speeds(indicated_airspeed=130, true_airspeed=138, ground_speed=140, mach=0.21, vertical_speed=-500),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=2200, fuel_flow_gph=7, oil_temp=185, oil_pressure=58)]),
        surfaces=SurfaceState(gear_handle=False, flaps_percent=0, spoilers_percent=0),
        flight_phase=FlightPhase.DESCENT,
    )


@pytest.fixture
def sim_state_approach() -> SimState:
    """Aircraft on approach, gear down, some flaps."""
    return _make_sim_state(
        position=Position(latitude=28.42, longitude=-81.32, altitude_msl=1500, altitude_agl=1400),
        attitude=Attitude(pitch=-2, bank=-5, heading_true=270, heading_magnetic=267),
        speeds=Speeds(indicated_airspeed=90, true_airspeed=92, ground_speed=88, mach=0.14, vertical_speed=-500),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=2100, fuel_flow_gph=8, oil_temp=190, oil_pressure=60)]),
        surfaces=SurfaceState(gear_handle=True, flaps_percent=50, spoilers_percent=0),
        flight_phase=FlightPhase.APPROACH,
    )


@pytest.fixture
def sim_state_landing() -> SimState:
    """Aircraft on short final, about to touch down."""
    return _make_sim_state(
        position=Position(latitude=28.4295, longitude=-81.3095, altitude_msl=146, altitude_agl=50),
        attitude=Attitude(pitch=3, bank=0, heading_true=270, heading_magnetic=267),
        speeds=Speeds(indicated_airspeed=65, true_airspeed=66, ground_speed=62, mach=0.10, vertical_speed=-300),
        engines=Engines(engine_count=1, engines=[_make_engine(rpm=1500, fuel_flow_gph=5, oil_temp=185, oil_pressure=58)]),
        surfaces=SurfaceState(gear_handle=True, flaps_percent=75, spoilers_percent=0),
        flight_phase=FlightPhase.LANDING,
    )


# ---------------------------------------------------------------------------
# Sample JSON payloads (matching the actual bridge broadcast format)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_bridge_broadcast() -> dict[str, Any]:
    """A raw JSON payload as broadcast by the SimConnect bridge."""
    return {
        "timestamp": "2026-03-20T23:22:23.8472527+00:00",
        "connected": True,
        "aircraft": "Cessna 172 Skyhawk",
        "position": {"latitude": 28.4294, "longitude": -81.309, "altitude_msl": 6500, "altitude_agl": 6400},
        "attitude": {"pitch": 2, "bank": 0, "heading_true": 45, "heading_magnetic": 42},
        "speeds": {"indicated_airspeed": 120, "true_airspeed": 130, "ground_speed": 135, "mach": 0.20, "vertical_speed": 50},
        "engines": {
            "engine_count": 1,
            "engines": [
                {"rpm": 2400, "manifold_pressure": 24.0, "fuel_flow_gph": 9.0, "egt": 1200, "oil_temp": 190, "oil_pressure": 60},
                {"rpm": 0, "manifold_pressure": 0, "fuel_flow_gph": 0, "egt": 0, "oil_temp": 0, "oil_pressure": 0},
                {"rpm": 0, "manifold_pressure": 0, "fuel_flow_gph": 0, "egt": 0, "oil_temp": 0, "oil_pressure": 0},
                {"rpm": 0, "manifold_pressure": 0, "fuel_flow_gph": 0, "egt": 0, "oil_temp": 0, "oil_pressure": 0},
            ],
        },
        "autopilot": {"master": True, "heading": 45, "altitude": 6500, "vertical_speed": 0, "airspeed": 0},
        "radios": {"com1": 121.7, "com2": 118.3, "nav1": 110.3, "nav2": 110.5},
        "fuel": {"total_gallons": 42, "total_weight_lbs": 252},
        "environment": {"wind_speed_kts": 5, "wind_direction": 270, "visibility_sm": 10, "temperature_c": 25, "barometer_inhg": 29.92},
        "surfaces": {"gear_handle": False, "flaps_percent": 0, "spoilers_percent": 0},
    }


@pytest.fixture
def sample_state_json(sample_bridge_broadcast: dict[str, Any]) -> dict[str, Any]:
    """Alias for backwards compat — returns the bridge broadcast format."""
    return sample_bridge_broadcast


@pytest.fixture
def sample_bridge_broadcast_message(sample_bridge_broadcast: dict[str, Any]) -> str:
    """A WebSocket message as the bridge would broadcast (raw JSON, no wrapper)."""
    return json.dumps(sample_bridge_broadcast)


@pytest.fixture
def sample_state_update_message(sample_bridge_broadcast: dict[str, Any]) -> str:
    """Legacy alias — now returns a raw broadcast (not wrapped in type/data)."""
    return json.dumps(sample_bridge_broadcast)


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
