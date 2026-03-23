"""Tests for orchestrator.game_state models and GameStateClient."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.game_state import (
    CombatState,
    GameActivity,
    GameState,
    PlayerStatus,
    ShipStatus,
)
from orchestrator.health import ConnectionState, HealthMonitor, SubsystemHealth
from orchestrator.game_client import GameStateClient


# ---------------------------------------------------------------------------
# GameState model parsing
# ---------------------------------------------------------------------------


class TestGameStateParsing:
    """Test that GameState correctly parses from dict payloads and applies defaults."""

    def test_parse_full_payload(
        self, sample_game_state_dict: dict[str, Any]
    ) -> None:
        state = GameState.model_validate(sample_game_state_dict)
        assert state.activity == GameActivity.SHIP_FLIGHT
        assert state.ship.name == "Anvil F7C-M Super Hornet"
        assert state.player.location_system == "Stanton"
        assert state.player.in_ship is True
        assert state.combat.under_attack is False

    def test_parse_minimal_payload_uses_defaults(self) -> None:
        state = GameState.model_validate({})
        assert state.activity == GameActivity.IDLE
        assert state.ship.name == ""
        assert state.player.location_system == ""
        assert state.combat.under_attack is False
        assert state.confidence == 0.0

    def test_parse_activity_enum(self) -> None:
        state = GameState.model_validate({"activity": "COMBAT"})
        assert state.activity == GameActivity.COMBAT

    def test_parse_invalid_activity_raises(self) -> None:
        with pytest.raises(Exception):
            GameState.model_validate({"activity": "HOVERING"})

    def test_timestamp_default_empty(self) -> None:
        state = GameState()
        assert state.timestamp == ""

    def test_raw_log_events_default_empty(self) -> None:
        state = GameState()
        assert state.raw_log_events == []

    def test_vision_data_default_empty(self) -> None:
        state = GameState()
        assert state.vision_data == {}

    def test_state_summary_returns_string(self) -> None:
        state = GameState()
        summary = state.state_summary()
        assert isinstance(summary, str)
        assert "Activity: IDLE" in summary


# ---------------------------------------------------------------------------
# ShipStatus
# ---------------------------------------------------------------------------


class TestShipStatus:
    """Test ShipStatus model including shield calculations and fuel."""

    def test_shields_percent_all_full(self) -> None:
        ship = ShipStatus()
        assert ship.shields_percent == pytest.approx(100.0)

    def test_shields_percent_partial(self) -> None:
        ship = ShipStatus(
            shields_front=60.0,
            shields_rear=80.0,
            shields_left=90.0,
            shields_right=70.0,
        )
        expected = (60.0 + 80.0 + 90.0 + 70.0) / 4.0
        assert ship.shields_percent == pytest.approx(expected)

    def test_shields_percent_all_zero(self) -> None:
        ship = ShipStatus(
            shields_front=0.0,
            shields_rear=0.0,
            shields_left=0.0,
            shields_right=0.0,
        )
        assert ship.shields_percent == pytest.approx(0.0)

    def test_fuel_defaults(self) -> None:
        ship = ShipStatus()
        assert ship.quantum_fuel_percent == 100.0
        assert ship.hydrogen_fuel_percent == 100.0

    def test_weapons_default_not_armed(self) -> None:
        ship = ShipStatus()
        assert ship.weapons_armed is False

    def test_quantum_drive_defaults(self) -> None:
        ship = ShipStatus()
        assert ship.quantum_drive_spooling is False
        assert ship.quantum_drive_active is False

    def test_power_default_off(self) -> None:
        ship = ShipStatus()
        assert ship.power_on is False

    def test_speed_defaults(self) -> None:
        ship = ShipStatus()
        assert ship.speed_scm == 0.0
        assert ship.speed_max == 0.0

    def test_missiles_default_zero(self) -> None:
        ship = ShipStatus()
        assert ship.missiles_remaining == 0


# ---------------------------------------------------------------------------
# PlayerStatus
# ---------------------------------------------------------------------------


class TestPlayerStatus:
    """Test PlayerStatus model for location and crime_stat."""

    def test_location_defaults_empty(self) -> None:
        player = PlayerStatus()
        assert player.location_system == ""
        assert player.location_body == ""
        assert player.location_zone == ""

    def test_location_tuple(self) -> None:
        player = PlayerStatus(
            location_system="Stanton",
            location_body="Hurston",
            location_zone="Lorville",
        )
        assert player.location_system == "Stanton"
        assert player.location_body == "Hurston"
        assert player.location_zone == "Lorville"

    def test_crime_stat_default_zero(self) -> None:
        player = PlayerStatus()
        assert player.crime_stat == 0

    def test_crime_stat_set(self) -> None:
        player = PlayerStatus(crime_stat=3)
        assert player.crime_stat == 3

    def test_not_in_ship_by_default(self) -> None:
        player = PlayerStatus()
        assert player.in_ship is False
        assert player.in_vehicle is False


# ---------------------------------------------------------------------------
# CombatState
# ---------------------------------------------------------------------------


class TestCombatState:
    """Test CombatState model fields."""

    def test_defaults_peaceful(self) -> None:
        combat = CombatState()
        assert combat.under_attack is False
        assert combat.hostile_count == 0
        assert combat.target_name == ""

    def test_under_attack(self) -> None:
        combat = CombatState(under_attack=True, hostile_count=2)
        assert combat.under_attack is True
        assert combat.hostile_count == 2

    def test_target_tracking(self) -> None:
        combat = CombatState(
            target_name="Cutlass Black",
            target_distance_km=1.5,
        )
        assert combat.target_name == "Cutlass Black"
        assert combat.target_distance_km == pytest.approx(1.5)

    def test_friendly_count(self) -> None:
        combat = CombatState(friendly_count=4)
        assert combat.friendly_count == 4


# ---------------------------------------------------------------------------
# state_summary() output
# ---------------------------------------------------------------------------


class TestStateSummary:
    """Test state_summary() output for various activities."""

    def test_idle_shows_activity(self, game_state_idle: GameState) -> None:
        summary = game_state_idle.state_summary()
        assert "Activity: IDLE" in summary

    def test_idle_shows_location(self, game_state_idle: GameState) -> None:
        summary = game_state_idle.state_summary()
        assert "Location: Stanton/Hurston/Lorville" in summary

    def test_combat_shows_under_attack(self, game_state_combat: GameState) -> None:
        summary = game_state_combat.state_summary()
        assert "UNDER ATTACK" in summary

    def test_combat_shows_hostile_count(self, game_state_combat: GameState) -> None:
        summary = game_state_combat.state_summary()
        assert "Hostiles: 3" in summary

    def test_combat_shows_weapons_hot(self, game_state_combat: GameState) -> None:
        summary = game_state_combat.state_summary()
        assert "WEAPONS:HOT" in summary

    def test_ship_flight_shows_ship_name(
        self, game_state_ship_flight: GameState
    ) -> None:
        summary = game_state_ship_flight.state_summary()
        assert "Ship: Anvil F7C-M Super Hornet" in summary

    def test_ship_flight_shows_shields(
        self, game_state_ship_flight: GameState
    ) -> None:
        summary = game_state_ship_flight.state_summary()
        assert "Shields: 100%" in summary

    def test_ship_flight_shows_fuel(
        self, game_state_ship_flight: GameState
    ) -> None:
        summary = game_state_ship_flight.state_summary()
        assert "QFuel: 85%" in summary
        assert "HFuel: 80%" in summary

    def test_quantum_travel_shows_qt_active(
        self, game_state_quantum_travel: GameState
    ) -> None:
        summary = game_state_quantum_travel.state_summary()
        assert "QT:ACTIVE" in summary

    def test_location_format_system_only(self) -> None:
        state = GameState(
            player=PlayerStatus(location_system="Pyro"),
        )
        summary = state.state_summary()
        assert "Location: Pyro" in summary

    def test_location_format_system_and_body(self) -> None:
        state = GameState(
            player=PlayerStatus(
                location_system="Stanton",
                location_body="ArcCorp",
            ),
        )
        summary = state.state_summary()
        assert "Location: Stanton/ArcCorp" in summary

    def test_location_format_full(self) -> None:
        state = GameState(
            player=PlayerStatus(
                location_system="Stanton",
                location_body="Hurston",
                location_zone="Lorville",
            ),
        )
        summary = state.state_summary()
        assert "Location: Stanton/Hurston/Lorville" in summary

    def test_crime_stat_shown_when_nonzero(self) -> None:
        state = GameState(
            player=PlayerStatus(crime_stat=2),
        )
        summary = state.state_summary()
        assert "CrimeStat: 2" in summary

    def test_crime_stat_hidden_when_zero(self, game_state_idle: GameState) -> None:
        summary = game_state_idle.state_summary()
        assert "CrimeStat" not in summary

    def test_decoupled_mode_shown(self) -> None:
        state = GameState(
            activity=GameActivity.SHIP_FLIGHT,
            player=PlayerStatus(in_ship=True),
            ship=ShipStatus(name="Aurora MR", decoupled_mode=True),
        )
        summary = state.state_summary()
        assert "DECOUPLED" in summary


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------


class TestHealthMonitor:
    """Test the subsystem health monitoring (from health.py)."""

    def test_register_creates_unhealthy_entry(self) -> None:
        monitor = HealthMonitor()
        monitor.register("test_subsystem")
        sub = monitor.get("test_subsystem")
        assert sub is not None
        assert sub.healthy is False
        assert sub.age_seconds == float("inf")

    def test_update_sets_healthy(self) -> None:
        monitor = HealthMonitor()
        monitor.register("svc")
        monitor.update("svc", True, "OK")
        sub = monitor.get("svc")
        assert sub is not None
        assert sub.healthy is True
        assert sub.message == "OK"
        assert sub.age_seconds < 1.0

    def test_update_auto_registers(self) -> None:
        monitor = HealthMonitor()
        monitor.update("new_svc", False, "down")
        sub = monitor.get("new_svc")
        assert sub is not None
        assert sub.healthy is False

    def test_all_healthy(self) -> None:
        monitor = HealthMonitor()
        monitor.update("a", True)
        monitor.update("b", True)
        assert monitor.all_healthy() is True
        monitor.update("b", False)
        assert monitor.all_healthy() is False

    def test_summary_returns_all_subsystems(self) -> None:
        monitor = HealthMonitor()
        monitor.update("x", True, "good")
        monitor.update("y", False, "bad")
        summary = monitor.summary()
        assert "x" in summary
        assert "y" in summary
        assert summary["x"]["healthy"] is True
        assert summary["y"]["healthy"] is False

    def test_subsystem_health_age(self) -> None:
        sub = SubsystemHealth(name="test")
        assert sub.age_seconds == float("inf")
        sub.last_seen = time.monotonic()
        assert sub.age_seconds < 1.0

    def test_health_monitor_tracks_degraded_state(self) -> None:
        """Monitor should correctly report when subsystems are degraded."""
        monitor = HealthMonitor()
        monitor.update("game_state_client", True, "Connected")
        monitor.update("chromadb", False, "Unavailable")
        monitor.update("whisper", False, "Unreachable")
        monitor.update("claude_api", True, "Ready")

        assert monitor.all_healthy() is False

        summary = monitor.summary()
        assert summary["game_state_client"]["healthy"] is True
        assert summary["chromadb"]["healthy"] is False
        assert summary["whisper"]["healthy"] is False
        assert summary["claude_api"]["healthy"] is True


# ---------------------------------------------------------------------------
# GameStateClient
# ---------------------------------------------------------------------------


class TestGameStateClient:
    """Test GameStateClient connect/disconnect and subscription pattern."""

    def test_initial_state_is_default(self) -> None:
        client = GameStateClient()
        assert client.state.activity == GameActivity.IDLE
        assert client.state.ship.name == ""
        assert client.state.player.in_ship is False

    def test_initial_connection_state(self) -> None:
        client = GameStateClient()
        assert client.connection_state == ConnectionState.DISCONNECTED

    def test_last_update_age_infinity_when_no_updates(self) -> None:
        client = GameStateClient()
        assert client.last_update_age == float("inf")

    @pytest.mark.asyncio
    async def test_connect_sets_state_to_connected(self) -> None:
        client = GameStateClient()
        await client.connect()
        assert client.connection_state == ConnectionState.CONNECTED
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_sets_state_to_disconnected(self) -> None:
        client = GameStateClient()
        await client.connect()
        await client.disconnect()
        assert client.connection_state == ConnectionState.DISCONNECTED
        assert client._update_task is None

    @pytest.mark.asyncio
    async def test_get_state_returns_current_state(self) -> None:
        client = GameStateClient()
        state = await client.get_state()
        assert state.activity == GameActivity.IDLE

    @pytest.mark.asyncio
    async def test_subscribe_callback_called_on_update(self) -> None:
        client = GameStateClient(update_interval=0.05)
        callback = AsyncMock()
        client.subscribe(callback)

        await client.connect()
        # Allow at least one update cycle
        await asyncio.sleep(0.15)
        await client.disconnect()

        assert callback.await_count >= 1
        received_state = callback.call_args[0][0]
        assert isinstance(received_state, GameState)

    @pytest.mark.asyncio
    async def test_subscriber_exception_does_not_crash_loop(self) -> None:
        client = GameStateClient(update_interval=0.05)
        bad_callback = AsyncMock(side_effect=RuntimeError("boom"))
        good_callback = AsyncMock()
        client.subscribe(bad_callback)
        client.subscribe(good_callback)

        await client.connect()
        await asyncio.sleep(0.15)
        await client.disconnect()

        # Both should have been called; the error in bad_callback
        # should not prevent good_callback from running
        assert bad_callback.await_count >= 1
        assert good_callback.await_count >= 1

    @pytest.mark.asyncio
    async def test_state_composition_with_no_modules(self) -> None:
        """Without perception modules, state should still be valid."""
        client = GameStateClient(update_interval=0.05)
        await client.connect()
        await asyncio.sleep(0.15)
        await client.disconnect()

        state = await client.get_state()
        assert isinstance(state, GameState)
        assert state.timestamp != ""  # Should have been set by _compose_state

    @pytest.mark.asyncio
    async def test_connect_disconnect_idempotent(self) -> None:
        """Calling disconnect when not connected should not raise."""
        client = GameStateClient()
        await client.disconnect()  # Should not raise
        assert client.connection_state == ConnectionState.DISCONNECTED
