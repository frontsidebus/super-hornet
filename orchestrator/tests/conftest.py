"""Shared fixtures for Super Hornet orchestrator test suite."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.game_state import (
    CombatState,
    GameActivity,
    GameState,
    PlayerStatus,
    ShipStatus,
)
from orchestrator.health import ConnectionState, HealthMonitor, SubsystemHealth


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
        "ELEVENLABS_MODEL_ID": "eleven_multilingual_v2",
        "SC_GAME_LOG_PATH": "C:/Program Files/Roberts Space Industries/StarCitizen/LIVE/Game.log",
        "UEX_API_BASE_URL": "https://uexcorp.space/api/2.0",
        "VISION_ENABLED": "false",
        "INPUT_SIMULATION_ENABLED": "false",
        "WHISPER_MODEL": "tiny",
        "WHISPER_URL": "http://localhost:9090",
        "SCREEN_CAPTURE_ENABLED": "false",
        "SCREEN_CAPTURE_FPS": "2",
        "CLAUDE_MODEL": "claude-sonnet-4-20250514",
        "CHROMADB_URL": "http://chromadb-test:9999",
        "LOG_LEVEL": "DEBUG",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


# ---------------------------------------------------------------------------
# GameState factory helpers
# ---------------------------------------------------------------------------


def _make_game_state(**overrides: Any) -> GameState:
    """Build a GameState with sensible defaults, applying overrides."""
    defaults: dict[str, Any] = {
        "timestamp": "2026-03-20T00:00:00Z",
        "activity": GameActivity.IDLE,
    }
    defaults.update(overrides)
    return GameState(**defaults)


@pytest.fixture
def game_state_idle() -> GameState:
    """Player at a station, not in a ship."""
    return _make_game_state(
        activity=GameActivity.IDLE,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="Hurston",
            location_zone="Lorville",
            in_ship=False,
            credits_auec=50000,
        ),
    )


@pytest.fixture
def game_state_ship_idle() -> GameState:
    """Player in ship, powered on, at Lorville in Stanton."""
    return _make_game_state(
        activity=GameActivity.SHIP_IDLE,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="Hurston",
            location_zone="Lorville",
            in_ship=True,
            credits_auec=50000,
        ),
        ship=ShipStatus(
            name="Anvil F7C-M Super Hornet",
            power_on=True,
            landing_gear_down=True,
            hydrogen_fuel_percent=95.0,
            quantum_fuel_percent=90.0,
        ),
    )


@pytest.fixture
def game_state_ship_flight() -> GameState:
    """Flying in ship, shields up, weapons off."""
    return _make_game_state(
        activity=GameActivity.SHIP_FLIGHT,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="Hurston",
            location_zone="",
            in_ship=True,
        ),
        ship=ShipStatus(
            name="Anvil F7C-M Super Hornet",
            power_on=True,
            shields_up=True,
            weapons_armed=False,
            landing_gear_down=False,
            hydrogen_fuel_percent=80.0,
            quantum_fuel_percent=85.0,
            speed_scm=280.0,
        ),
    )


@pytest.fixture
def game_state_quantum_travel() -> GameState:
    """Quantum travel active, en route."""
    return _make_game_state(
        activity=GameActivity.QUANTUM_TRAVEL,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="",
            location_zone="",
            in_ship=True,
        ),
        ship=ShipStatus(
            name="Anvil F7C-M Super Hornet",
            power_on=True,
            shields_up=True,
            quantum_drive_active=True,
            quantum_fuel_percent=70.0,
            hydrogen_fuel_percent=80.0,
        ),
    )


@pytest.fixture
def game_state_combat() -> GameState:
    """Under attack, weapons armed, hostiles present."""
    return _make_game_state(
        activity=GameActivity.COMBAT,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="Crusader",
            location_zone="",
            in_ship=True,
        ),
        ship=ShipStatus(
            name="Anvil F7C-M Super Hornet",
            power_on=True,
            shields_up=True,
            shields_front=60.0,
            shields_rear=80.0,
            shields_left=90.0,
            shields_right=70.0,
            weapons_armed=True,
            missiles_remaining=4,
            speed_scm=320.0,
        ),
        combat=CombatState(
            under_attack=True,
            hostile_count=3,
            target_name="Cutlass Black",
            target_distance_km=1.2,
        ),
    )


@pytest.fixture
def game_state_mining() -> GameState:
    """Mining activity, in Prospector."""
    return _make_game_state(
        activity=GameActivity.MINING,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="ArcCorp",
            location_zone="Lyria",
            in_ship=True,
        ),
        ship=ShipStatus(
            name="MISC Prospector",
            power_on=True,
            shields_up=True,
            hydrogen_fuel_percent=60.0,
            quantum_fuel_percent=45.0,
        ),
    )


@pytest.fixture
def game_state_trading() -> GameState:
    """At a trade terminal."""
    return _make_game_state(
        activity=GameActivity.TRADING,
        player=PlayerStatus(
            location_system="Stanton",
            location_body="Hurston",
            location_zone="Lorville CBD",
            in_ship=False,
            credits_auec=125000,
        ),
    )


# ---------------------------------------------------------------------------
# Sample JSON payloads (matching the GameState model format)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_game_state_dict() -> dict[str, Any]:
    """A raw dict of GameState data as would be composed by GameStateClient."""
    return {
        "timestamp": "2026-03-20T23:22:23Z",
        "activity": "SHIP_FLIGHT",
        "ship": {
            "name": "Anvil F7C-M Super Hornet",
            "shields_up": True,
            "shields_front": 100.0,
            "shields_rear": 100.0,
            "shields_left": 100.0,
            "shields_right": 100.0,
            "hull_percent": 100.0,
            "quantum_fuel_percent": 85.0,
            "hydrogen_fuel_percent": 78.0,
            "power_on": True,
            "weapons_armed": False,
            "landing_gear_down": False,
            "quantum_drive_spooling": False,
            "quantum_drive_active": False,
            "missiles_remaining": 6,
            "speed_scm": 280.0,
            "speed_max": 1236.0,
            "decoupled_mode": False,
        },
        "player": {
            "location_system": "Stanton",
            "location_body": "Hurston",
            "location_zone": "Lorville",
            "credits_auec": 50000,
            "in_ship": True,
            "in_vehicle": False,
            "crime_stat": 0,
        },
        "combat": {
            "under_attack": False,
            "target_name": "",
            "target_distance_km": 0.0,
            "hostile_count": 0,
            "friendly_count": 0,
            "last_kill": "",
            "last_death": "",
        },
        "raw_log_events": [],
        "vision_data": {},
        "confidence": 0.75,
    }


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
        "metadatas": [[{"source": "sc_manual.pdf", "chunk_index": 0}, {"source": "sc_manual.pdf", "chunk_index": 1}]],
        "distances": [[0.12, 0.25]],
    }
    coll.upsert = MagicMock()
    return coll
