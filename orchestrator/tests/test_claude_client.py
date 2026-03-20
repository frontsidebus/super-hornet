"""Tests for orchestrator.claude_client — system prompt building, tool defs, message assembly."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.claude_client import (
    MERLIN_PERSONA,
    TOOL_DEFINITIONS,
    ClaudeClient,
)
from orchestrator.sim_client import (
    AutopilotState,
    Environment,
    FlightPhase,
    SimState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sim_client() -> MagicMock:
    client = MagicMock()
    client.get_state = AsyncMock(return_value=SimState())
    return client


@pytest.fixture
def mock_context_store() -> MagicMock:
    store = MagicMock()
    store.get_relevant_context = AsyncMock(return_value=[])
    store.query = AsyncMock(return_value=[])
    return store


@pytest.fixture
def claude_client(mock_sim_client: MagicMock, mock_context_store: MagicMock) -> ClaudeClient:
    with patch("orchestrator.claude_client.anthropic.AsyncAnthropic"):
        return ClaudeClient(
            api_key="sk-ant-test",
            model="claude-sonnet-4-20250514",
            sim_client=mock_sim_client,
            context_store=mock_context_store,
        )


# ---------------------------------------------------------------------------
# Tool definitions structure
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    """Verify the TOOL_DEFINITIONS list has the required shape for the Anthropic API."""

    def test_all_tools_have_required_keys(self) -> None:
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_get_sim_state_has_no_required_params(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_sim_state")
        assert tool["input_schema"]["required"] == []

    def test_lookup_airport_requires_identifier(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "lookup_airport")
        assert "identifier" in tool["input_schema"]["required"]

    def test_search_manual_requires_query(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_manual")
        assert "query" in tool["input_schema"]["required"]

    def test_get_checklist_requires_phase(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_checklist")
        assert "phase" in tool["input_schema"]["required"]

    def test_create_flight_plan_requires_departure_and_destination(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "create_flight_plan")
        required = tool["input_schema"]["required"]
        assert "departure" in required
        assert "destination" in required

    def test_five_tools_defined(self) -> None:
        assert len(TOOL_DEFINITIONS) == 5

    def test_tool_names_are_unique(self) -> None:
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# System prompt building
# ---------------------------------------------------------------------------


class TestSystemPromptBuilding:
    """Test _build_system_prompt with various SimState and context combinations."""

    def test_prompt_contains_persona(self, claude_client: ClaudeClient) -> None:
        prompt = claude_client._build_system_prompt(SimState(), [])
        assert "MERLIN" in prompt
        assert "Captain" in prompt

    def test_prompt_contains_telemetry_summary(self, claude_client: ClaudeClient, sim_state_cruise: SimState) -> None:
        prompt = claude_client._build_system_prompt(sim_state_cruise, [])
        assert "CRUISE" in prompt
        assert "6500ft" in prompt

    def test_prompt_contains_aircraft_title(self, claude_client: ClaudeClient) -> None:
        state = SimState(aircraft_title="Cessna 172 Skyhawk")
        prompt = claude_client._build_system_prompt(state, [])
        assert "Cessna 172 Skyhawk" in prompt

    def test_prompt_unknown_aircraft(self, claude_client: ClaudeClient) -> None:
        state = SimState(aircraft_title="")
        prompt = claude_client._build_system_prompt(state, [])
        assert "Unknown" in prompt

    def test_prompt_includes_on_ground_status(self, claude_client: ClaudeClient) -> None:
        state = SimState(on_ground=True)
        prompt = claude_client._build_system_prompt(state, [])
        assert "On ground: True" in prompt

    def test_prompt_includes_autopilot_when_engaged(self, claude_client: ClaudeClient) -> None:
        state = SimState(
            autopilot=AutopilotState(
                master=True, set_heading=270, set_altitude=6500, set_vertical_speed=-500,
            )
        )
        prompt = claude_client._build_system_prompt(state, [])
        assert "Autopilot:" in prompt
        assert "HDG 270" in prompt
        assert "ALT 6500" in prompt

    def test_prompt_excludes_autopilot_when_disengaged(self, claude_client: ClaudeClient) -> None:
        state = SimState(autopilot=AutopilotState(master=False))
        prompt = claude_client._build_system_prompt(state, [])
        assert "Autopilot:" not in prompt

    def test_prompt_includes_weather(self, claude_client: ClaudeClient) -> None:
        state = SimState(
            environment=Environment(wind_speed=12, wind_direction=270, visibility=10, temperature=20, pressure=30.12),
        )
        prompt = claude_client._build_system_prompt(state, [])
        assert "Wind" in prompt
        assert "Vis" in prompt
        assert "Temp" in prompt
        assert "QNH" in prompt

    def test_prompt_includes_context_docs(self, claude_client: ClaudeClient) -> None:
        docs = [
            {"content": "Engine runup procedure for Cessna 172...", "metadata": {"source": "poh.pdf"}},
            {"content": "Normal takeoff checklist...", "metadata": {"source": "checklist.pdf"}},
        ]
        prompt = claude_client._build_system_prompt(SimState(), docs)
        assert "RELEVANT REFERENCE MATERIAL" in prompt
        assert "poh.pdf" in prompt
        assert "Engine runup" in prompt

    def test_prompt_limits_context_to_three_docs(self, claude_client: ClaudeClient) -> None:
        docs = [
            {"content": f"Doc {i}", "metadata": {"source": f"src{i}.pdf"}}
            for i in range(5)
        ]
        prompt = claude_client._build_system_prompt(SimState(), docs)
        assert "src0.pdf" in prompt
        assert "src2.pdf" in prompt
        assert "src3.pdf" not in prompt

    def test_prompt_truncates_long_content(self, claude_client: ClaudeClient) -> None:
        docs = [{"content": "X" * 1000, "metadata": {"source": "big.pdf"}}]
        prompt = claude_client._build_system_prompt(SimState(), docs)
        # Content should be truncated to 500 chars
        assert "X" * 500 in prompt
        assert "X" * 501 not in prompt


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------


class TestConversationManagement:
    """Test history trimming and clearing."""

    def test_clear_history_empties_conversation(self, claude_client: ClaudeClient) -> None:
        claude_client._conversation = [{"role": "user", "content": "hi"}] * 10
        claude_client.clear_history()
        assert len(claude_client._conversation) == 0

    def test_trim_history_keeps_within_limit(self, claude_client: ClaudeClient) -> None:
        claude_client._max_history = 5
        claude_client._conversation = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        claude_client._trim_history()
        assert len(claude_client._conversation) == 10  # 5 * 2

    def test_trim_history_no_op_when_within_limit(self, claude_client: ClaudeClient) -> None:
        claude_client._max_history = 50
        claude_client._conversation = [{"role": "user", "content": "hi"}] * 5
        claude_client._trim_history()
        assert len(claude_client._conversation) == 5


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


class TestToolExecution:
    """Test _execute_tool dispatches to the correct tool functions."""

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, claude_client: ClaudeClient) -> None:
        result = await claude_client._execute_tool("nonexistent_tool", {}, SimState())
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_catches_exceptions(self, claude_client: ClaudeClient) -> None:
        with patch("orchestrator.claude_client.get_sim_state", side_effect=RuntimeError("sim offline")):
            result = await claude_client._execute_tool("get_sim_state", {}, SimState())
            assert "error" in result
            assert "sim offline" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_get_sim_state(self, claude_client: ClaudeClient, mock_sim_client: MagicMock) -> None:
        mock_state = SimState(aircraft_title="Test Aircraft")
        mock_sim_client.get_state = AsyncMock(return_value=mock_state)
        with patch("orchestrator.claude_client.get_sim_state", new_callable=AsyncMock, return_value={"aircraft": "Test Aircraft"}) as mock_fn:
            result = await claude_client._execute_tool("get_sim_state", {}, SimState())
            mock_fn.assert_awaited_once()
            assert result["aircraft"] == "Test Aircraft"

    @pytest.mark.asyncio
    async def test_execute_lookup_airport(self, claude_client: ClaudeClient) -> None:
        with patch("orchestrator.claude_client.lookup_airport", new_callable=AsyncMock, return_value={"identifier": "KJFK"}) as mock_fn:
            result = await claude_client._execute_tool("lookup_airport", {"identifier": "KJFK"}, SimState())
            mock_fn.assert_awaited_once_with("KJFK")
            assert result["identifier"] == "KJFK"

    @pytest.mark.asyncio
    async def test_execute_search_manual(self, claude_client: ClaudeClient) -> None:
        state = SimState(aircraft_title="Cessna 172")
        with patch("orchestrator.claude_client.search_manual", new_callable=AsyncMock, return_value=[]) as mock_fn:
            result = await claude_client._execute_tool("search_manual", {"query": "V-speeds"}, state)
            mock_fn.assert_awaited_once()
            call_kwargs = mock_fn.call_args
            assert call_kwargs[0][0] == "V-speeds"
            assert call_kwargs[1]["aircraft_type"] == "Cessna 172"

    @pytest.mark.asyncio
    async def test_execute_get_checklist(self, claude_client: ClaudeClient) -> None:
        with patch("orchestrator.claude_client.get_checklist", new_callable=AsyncMock, return_value={"phase": "PREFLIGHT"}) as mock_fn:
            result = await claude_client._execute_tool("get_checklist", {"phase": "PREFLIGHT"}, SimState())
            mock_fn.assert_awaited_once()
            assert result["phase"] == "PREFLIGHT"

    @pytest.mark.asyncio
    async def test_execute_create_flight_plan(self, claude_client: ClaudeClient) -> None:
        with patch("orchestrator.claude_client.create_flight_plan", new_callable=AsyncMock, return_value={"status": "draft"}) as mock_fn:
            result = await claude_client._execute_tool(
                "create_flight_plan",
                {"departure": "KJFK", "destination": "KLAX", "altitude": 35000, "route": "J80"},
                SimState(),
            )
            mock_fn.assert_awaited_once()
            assert result["status"] == "draft"
