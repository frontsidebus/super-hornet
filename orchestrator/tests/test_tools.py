"""Tests for orchestrator.tools — tool function implementations."""

from __future__ import annotations

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
from orchestrator.game_client import GameStateClient
from orchestrator.tools import (
    DEFAULT_PROCEDURES,
    get_game_state,
    get_procedure,
    get_ship_status,
    get_skill,
    lookup_commodity,
    plan_trade_route,
    search_knowledge,
)


# ---------------------------------------------------------------------------
# get_game_state
# ---------------------------------------------------------------------------


class TestGetGameState:
    """Test formatted game state retrieval."""

    @pytest.mark.asyncio
    async def test_returns_formatted_dict(self) -> None:
        state = GameState(
            activity=GameActivity.SHIP_FLIGHT,
            player=PlayerStatus(
                in_ship=True,
                location_system="Stanton",
                location_body="Crusader",
                location_zone="Port Olisar",
            ),
            ship=ShipStatus(
                name="Super Hornet",
                shields_front=100.0,
                shields_rear=100.0,
                hull_percent=100.0,
                hydrogen_fuel_percent=85.0,
                quantum_fuel_percent=92.0,
                power_on=True,
            ),
            combat=CombatState(under_attack=False, hostile_count=0),
        )
        mock_client = MagicMock(spec=GameStateClient)
        mock_client.get_state = AsyncMock(return_value=state)

        result = await get_game_state(mock_client)

        # The tool calls state.to_dict(), so verify it returns dict-like output
        # with SC-relevant fields
        assert isinstance(result, dict)
        assert result["activity"] == "SHIP_FLIGHT"
        assert result["ship"]["name"] == "Super Hornet"
        assert result["player"]["location_system"] == "Stanton"
        assert result["combat"]["under_attack"] is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_error_dict(self) -> None:
        mock_client = MagicMock(spec=GameStateClient)
        mock_client.get_state = AsyncMock(side_effect=ConnectionError("No game"))

        result = await get_game_state(mock_client)
        assert "error" in result
        assert "Not connected" in result["error"]

    @pytest.mark.asyncio
    async def test_generic_exception_returns_error_dict(self) -> None:
        mock_client = MagicMock(spec=GameStateClient)
        mock_client.get_state = AsyncMock(side_effect=RuntimeError("boom"))

        result = await get_game_state(mock_client)
        assert "error" in result
        assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# lookup_commodity
# ---------------------------------------------------------------------------


class TestLookupCommodity:
    """Test commodity lookup with mocked UEX client."""

    @pytest.mark.asyncio
    async def test_successful_lookup(self) -> None:
        mock_uex = MagicMock()
        mock_uex.lookup_commodity = AsyncMock(return_value={
            "commodity": "Laranite",
            "buy_price": 2750,
            "sell_price": 3100,
        })

        result = await lookup_commodity("Laranite", mock_uex)
        assert result["commodity"] == "Laranite"
        assert result["buy_price"] == 2750
        mock_uex.lookup_commodity.assert_awaited_once_with("Laranite", location="")

    @pytest.mark.asyncio
    async def test_lookup_with_location_filter(self) -> None:
        mock_uex = MagicMock()
        mock_uex.lookup_commodity = AsyncMock(return_value={"commodity": "Agricium"})

        result = await lookup_commodity("Agricium", mock_uex, location="Lorville")
        mock_uex.lookup_commodity.assert_awaited_once_with("Agricium", location="Lorville")
        assert result["commodity"] == "Agricium"

    @pytest.mark.asyncio
    async def test_empty_commodity_returns_error(self) -> None:
        mock_uex = MagicMock()
        result = await lookup_commodity("", mock_uex)
        assert "error" in result
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        mock_uex = MagicMock()
        mock_uex.lookup_commodity = AsyncMock(side_effect=RuntimeError("API down"))

        result = await lookup_commodity("Laranite", mock_uex)
        assert "error" in result
        assert "API down" in result["error"]


# ---------------------------------------------------------------------------
# search_knowledge
# ---------------------------------------------------------------------------


class TestSearchKnowledge:
    """Test knowledge base search with mocked context store."""

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[
            {"content": "Shield management for Anvil ships...", "metadata": {"source": "sc_manual.pdf"}, "distance": 0.1},
            {"content": "Power triangle distribution...", "metadata": {"source": "combat_guide.pdf"}, "distance": 0.2},
        ])

        result = await search_knowledge("shield management", mock_store)
        assert len(result) == 2
        assert result[0]["content"] == "Shield management for Anvil ships..."
        assert result[0]["source"] == "sc_manual.pdf"

    @pytest.mark.asyncio
    async def test_passes_ship_name_filter(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        await search_knowledge("weapons", mock_store, ship_name="Super Hornet")
        mock_store.query.assert_awaited_once()
        call_kwargs = mock_store.query.call_args[1]
        assert call_kwargs["filters"] == {"ship_name": "Super Hornet"}

    @pytest.mark.asyncio
    async def test_no_ship_name_passes_no_filter(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        await search_knowledge("quantum travel", mock_store, ship_name="")
        call_kwargs = mock_store.query.call_args[1]
        assert call_kwargs["filters"] is None

    @pytest.mark.asyncio
    async def test_custom_n_results(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        await search_knowledge("mining", mock_store, n_results=3)
        call_kwargs = mock_store.query.call_args[1]
        assert call_kwargs["n_results"] == 3

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await search_knowledge("nonexistent topic", mock_store)
        assert result == []


# ---------------------------------------------------------------------------
# get_ship_status
# ---------------------------------------------------------------------------


class TestGetShipStatus:
    """Test ship status retrieval with mocked game client."""

    @pytest.mark.asyncio
    async def test_returns_ship_status(self) -> None:
        mock_client = MagicMock(spec=GameStateClient)
        mock_client.get_ship_status = AsyncMock(return_value={
            "name": "Super Hornet",
            "shields_percent": 85.0,
            "hull_percent": 100.0,
            "hydrogen_fuel_percent": 72.0,
            "quantum_fuel_percent": 90.0,
            "power_on": True,
            "weapons_armed": True,
            "landing_gear_down": False,
        })

        result = await get_ship_status(mock_client)
        assert result["name"] == "Super Hornet"
        assert result["shields_percent"] == 85.0
        assert result["power_on"] is True
        assert result["weapons_armed"] is True

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self) -> None:
        mock_client = MagicMock(spec=GameStateClient)
        mock_client.get_ship_status = AsyncMock(side_effect=ConnectionError("No game"))

        result = await get_ship_status(mock_client)
        assert "error" in result
        assert "Not connected" in result["error"]

    @pytest.mark.asyncio
    async def test_generic_exception_returns_error(self) -> None:
        mock_client = MagicMock(spec=GameStateClient)
        mock_client.get_ship_status = AsyncMock(side_effect=RuntimeError("fail"))

        result = await get_ship_status(mock_client)
        assert "error" in result
        assert "fail" in result["error"]


# ---------------------------------------------------------------------------
# plan_trade_route
# ---------------------------------------------------------------------------


class TestPlanTradeRoute:
    """Test trade route planning with mocked UEX client."""

    @pytest.mark.asyncio
    async def test_basic_trade_route(self) -> None:
        mock_uex = MagicMock()
        mock_uex.plan_trade_route = AsyncMock(return_value={
            "origin": "Lorville",
            "destination": "New Babbage",
            "commodity": "Laranite",
            "profit": 15000,
            "status": "planned",
        })

        result = await plan_trade_route("Lorville", "New Babbage", 100, mock_uex)
        assert result["origin"] == "Lorville"
        assert result["destination"] == "New Babbage"
        assert result["profit"] == 15000
        mock_uex.plan_trade_route.assert_awaited_once_with(
            origin="Lorville",
            destination="New Babbage",
            cargo_scu=100,
        )

    @pytest.mark.asyncio
    async def test_missing_origin_returns_error(self) -> None:
        mock_uex = MagicMock()
        result = await plan_trade_route("", "New Babbage", 100, mock_uex)
        assert "error" in result
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_destination_returns_error(self) -> None:
        mock_uex = MagicMock()
        result = await plan_trade_route("Lorville", "", 100, mock_uex)
        assert "error" in result
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_cargo_scu_returns_error(self) -> None:
        mock_uex = MagicMock()
        result = await plan_trade_route("Lorville", "New Babbage", 0, mock_uex)
        assert "error" in result
        assert "positive" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        mock_uex = MagicMock()
        mock_uex.plan_trade_route = AsyncMock(side_effect=RuntimeError("UEX down"))

        result = await plan_trade_route("Lorville", "New Babbage", 100, mock_uex)
        assert "error" in result
        assert "UEX down" in result["error"]


# ---------------------------------------------------------------------------
# get_procedure
# ---------------------------------------------------------------------------


class TestGetProcedure:
    """Test procedure retrieval and activity filtering."""

    def test_default_procedures_cover_expected_activities(self) -> None:
        expected_activities = {
            GameActivity.SHIP_IDLE,
            GameActivity.SHIP_FLIGHT,
            GameActivity.QUANTUM_TRAVEL,
            GameActivity.COMBAT,
            GameActivity.MINING,
            GameActivity.SALVAGE,
            GameActivity.TRADING,
            GameActivity.LANDING,
        }
        for activity in expected_activities:
            assert activity in DEFAULT_PROCEDURES, (
                f"Missing default procedure for {activity.value}"
            )

    @pytest.mark.asyncio
    async def test_default_procedure_for_combat(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_procedure("COMBAT", mock_store)
        assert result["activity"] == "COMBAT"
        assert result["source"] == "default"
        assert "items" in result
        assert len(result["items"]) > 0

    @pytest.mark.asyncio
    async def test_case_insensitive_activity(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_procedure("combat", mock_store)
        assert result["activity"] == "COMBAT"

    @pytest.mark.asyncio
    async def test_invalid_activity_returns_error(self) -> None:
        mock_store = MagicMock()
        result = await get_procedure("WARP_DRIVE", mock_store)
        assert "error" in result
        assert "Unknown activity" in result["error"]

    @pytest.mark.asyncio
    async def test_ship_specific_procedure_from_store(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[
            {
                "content": "Super Hornet combat procedure: 1. Set weapons to...",
                "metadata": {"source": "hornet_manual.pdf"},
            },
        ])

        result = await get_procedure("COMBAT", mock_store, ship_name="Super Hornet")
        assert result["source"] == "knowledge_base"
        assert result["ship"] == "Super Hornet"
        assert "Super Hornet combat" in result["procedure"]

    @pytest.mark.asyncio
    async def test_fallback_to_default_when_store_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_procedure("MINING", mock_store, ship_name="Prospector")
        assert result["source"] == "default"
        assert result["ship"] == "Prospector"

    @pytest.mark.asyncio
    async def test_accepts_game_activity_enum_directly(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_procedure(GameActivity.QUANTUM_TRAVEL, mock_store)
        assert result["activity"] == "QUANTUM_TRAVEL"

    @pytest.mark.asyncio
    async def test_generic_ship_when_none_specified(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_procedure("LANDING", mock_store, ship_name="")
        assert result["ship"] == "generic"


# ---------------------------------------------------------------------------
# get_skill
# ---------------------------------------------------------------------------


class TestGetSkill:
    """Test skill library search with mocked SkillLibrary."""

    @pytest.mark.asyncio
    async def test_returns_skill_dict(self) -> None:
        mock_skill = MagicMock()
        mock_skill.to_dict.return_value = {
            "name": "quantum_travel",
            "description": "Initiate quantum travel to a destination",
            "steps": ["Open starmap", "Select destination", "Spool QD", "Engage"],
        }

        mock_library = MagicMock()
        mock_library.search = AsyncMock(return_value=mock_skill)

        result = await get_skill("quantum travel", mock_library)
        assert result["name"] == "quantum_travel"
        assert "steps" in result
        mock_library.search.assert_awaited_once_with("quantum travel")

    @pytest.mark.asyncio
    async def test_no_match_returns_error(self) -> None:
        mock_library = MagicMock()
        mock_library.search = AsyncMock(return_value=None)

        result = await get_skill("nonexistent skill", mock_library)
        assert "error" in result
        assert "No skill found" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self) -> None:
        mock_library = MagicMock()
        result = await get_skill("", mock_library)
        assert "error" in result
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        mock_library = MagicMock()
        mock_library.search = AsyncMock(side_effect=RuntimeError("DB error"))

        result = await get_skill("power on", mock_library)
        assert "error" in result
        assert "DB error" in result["error"]
