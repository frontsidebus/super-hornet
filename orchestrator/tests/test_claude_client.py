"""Tests for orchestrator.claude_client — system prompt, token budgets, query classification."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.claude_client import (
    MERLIN_PERSONA,
    STOP_SEQUENCES,
    TOOL_DEFINITIONS,
    ClaudeClient,
    classify_query,
    max_tokens_for_query,
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


@pytest.fixture
def claude_client_custom_tokens(
    mock_sim_client: MagicMock,
    mock_context_store: MagicMock,
) -> ClaudeClient:
    """ClaudeClient with custom token limits for testing."""
    with patch("orchestrator.claude_client.anthropic.AsyncAnthropic"):
        return ClaudeClient(
            api_key="sk-ant-test",
            model="claude-sonnet-4-20250514",
            sim_client=mock_sim_client,
            context_store=mock_context_store,
            max_tokens=512,
            max_tokens_briefing=1500,
            max_history=10,
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
# Query classification
# ---------------------------------------------------------------------------


class TestQueryClassification:
    """Test classify_query categorizes pilot messages correctly."""

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("roger", "short"),
            ("Roger that", "short"),
            ("copy", "short"),
            ("wilco", "short"),
            ("thanks", "short"),
            ("thank you", "short"),
            ("got it", "short"),
            ("ok", "short"),
            ("yes", "short"),
            ("no", "short"),
            ("What's my altitude?", "short"),
            ("What's our heading?", "short"),
            ("How much fuel do we have?", "short"),
            ("How far to destination?", "short"),
        ],
    )
    def test_short_queries(self, message: str, expected: str) -> None:
        assert classify_query(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Give me the approach briefing", "briefing"),
            ("Run the preflight checklist", "briefing"),
            ("Create a flight plan to KLAX", "briefing"),
            ("Walk me through the engine start", "briefing"),
            ("Explain how the autopilot works", "briefing"),
            ("How does the fuel system work?", "briefing"),
            ("Plan a flight from KJFK to KORD", "briefing"),
            ("Build a route to Chicago", "briefing"),
        ],
    )
    def test_briefing_queries(self, message: str, expected: str) -> None:
        assert classify_query(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("What airport is nearby?", "normal"),
            ("Should I descend now?", "normal"),
            ("What's the weather looking like?", "normal"),
            ("Can you check the NOTAMs?", "normal"),
        ],
    )
    def test_normal_queries(self, message: str, expected: str) -> None:
        assert classify_query(message) == expected


# ---------------------------------------------------------------------------
# Response token budgeting
# ---------------------------------------------------------------------------


class TestMaxTokensForQuery:
    """Test token budget allocation by query type."""

    def test_short_query_gets_256_or_less(self) -> None:
        tokens = max_tokens_for_query("short", default_max=1024, briefing_max=2048)
        assert tokens == 256

    def test_short_query_capped_by_default_max(self) -> None:
        tokens = max_tokens_for_query("short", default_max=128, briefing_max=2048)
        assert tokens == 128

    def test_briefing_query_gets_briefing_max(self) -> None:
        tokens = max_tokens_for_query("briefing", default_max=1024, briefing_max=2048)
        assert tokens == 2048

    def test_normal_query_gets_default_max(self) -> None:
        tokens = max_tokens_for_query("normal", default_max=1024, briefing_max=2048)
        assert tokens == 1024

    def test_default_values(self) -> None:
        assert max_tokens_for_query("short") == 256
        assert max_tokens_for_query("briefing") == 2048
        assert max_tokens_for_query("normal") == 1024


# ---------------------------------------------------------------------------
# System prompt building
# ---------------------------------------------------------------------------


class TestSystemPromptBuilding:
    """Test _build_system_prompt with various SimState and context combinations."""

    def test_prompt_contains_persona(self, claude_client: ClaudeClient) -> None:
        prompt = claude_client._build_system_prompt(SimState(), [])
        assert "MERLIN" in prompt
        assert "Captain" in prompt

    def test_prompt_contains_telemetry_summary(
        self,
        claude_client: ClaudeClient,
        sim_state_cruise: SimState,
    ) -> None:
        prompt = claude_client._build_system_prompt(sim_state_cruise, [])
        assert "CRUISE" in prompt
        assert "6500ft" in prompt

    def test_prompt_contains_aircraft(self, claude_client: ClaudeClient) -> None:
        state = SimState(aircraft="Cessna 172 Skyhawk")
        prompt = claude_client._build_system_prompt(state, [])
        assert "Cessna 172 Skyhawk" in prompt

    def test_prompt_unknown_aircraft(self, claude_client: ClaudeClient) -> None:
        state = SimState(aircraft="")
        prompt = claude_client._build_system_prompt(state, [])
        assert "Unknown" in prompt

    def test_prompt_includes_on_ground_status(self, claude_client: ClaudeClient) -> None:
        state = SimState()  # AGL=0 => on_ground=True
        prompt = claude_client._build_system_prompt(state, [])
        assert "On ground: True" in prompt

    def test_prompt_includes_autopilot_when_engaged(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = SimState(
            autopilot=AutopilotState(
                master=True, heading=270, altitude=6500, vertical_speed=-500,
            )
        )
        prompt = claude_client._build_system_prompt(state, [])
        assert "Autopilot:" in prompt
        assert "HDG 270" in prompt
        assert "ALT 6500" in prompt

    def test_prompt_excludes_autopilot_when_disengaged(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = SimState(autopilot=AutopilotState(master=False))
        prompt = claude_client._build_system_prompt(state, [])
        assert "Autopilot:" not in prompt

    def test_prompt_includes_weather(self, claude_client: ClaudeClient) -> None:
        state = SimState(
            environment=Environment(
                wind_speed_kts=12, wind_direction=270, visibility_sm=10,
                temperature_c=20, barometer_inhg=30.12,
            ),
        )
        prompt = claude_client._build_system_prompt(state, [])
        assert "Wind" in prompt
        assert "Vis" in prompt
        assert "Temp" in prompt
        assert "QNH" in prompt

    def test_prompt_includes_context_docs(self, claude_client: ClaudeClient) -> None:
        docs = [
            {
                "content": "Engine runup procedure for Cessna 172...",
                "metadata": {"source": "poh.pdf"},
            },
            {
                "content": "Normal takeoff checklist...",
                "metadata": {"source": "checklist.pdf"},
            },
        ]
        prompt = claude_client._build_system_prompt(SimState(), docs)
        assert "RELEVANT REFERENCE MATERIAL" in prompt
        assert "poh.pdf" in prompt
        assert "Engine runup" in prompt

    def test_prompt_limits_context_to_three_docs(
        self,
        claude_client: ClaudeClient,
    ) -> None:
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

    def test_prompt_includes_response_pacing_rules(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        prompt = claude_client._build_system_prompt(SimState(), [])
        assert "RESPONSE RULES" in prompt
        assert "brevity saves lives" in prompt
        assert "STOP" in prompt

    def test_prompt_includes_phase_style_for_approach(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = SimState(flight_phase=FlightPhase.APPROACH)
        prompt = claude_client._build_system_prompt(state, [])
        assert "ULTRA-BRIEF" in prompt
        assert "CURRENT RESPONSE STYLE" in prompt

    def test_prompt_includes_phase_style_for_cruise(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = SimState(flight_phase=FlightPhase.CRUISE)
        prompt = claude_client._build_system_prompt(state, [])
        assert "Conversational" in prompt
        assert "teach" in prompt

    def test_prompt_includes_phase_style_for_takeoff(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = SimState(flight_phase=FlightPhase.TAKEOFF)
        prompt = claude_client._build_system_prompt(state, [])
        assert "ULTRA-BRIEF" in prompt
        assert "Callouts only" in prompt


# ---------------------------------------------------------------------------
# Stop sequences
# ---------------------------------------------------------------------------


class TestStopSequences:
    """Verify stop sequences are defined for natural conversation breaks."""

    def test_stop_sequences_exist(self) -> None:
        assert len(STOP_SEQUENCES) > 0

    def test_stop_sequences_include_over(self) -> None:
        lower_seqs = [s.strip().lower() for s in STOP_SEQUENCES]
        assert "over." in lower_seqs or "over" in lower_seqs


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------


class TestConversationManagement:
    """Test history trimming and clearing."""

    def test_clear_history_empties_conversation(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        claude_client._conversation = [{"role": "user", "content": "hi"}] * 10
        claude_client.clear_history()
        assert len(claude_client._conversation) == 0

    def test_trim_history_keeps_within_limit(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        claude_client._max_history = 5
        claude_client._conversation = [
            {"role": "user", "content": f"msg {i}"} for i in range(20)
        ]
        claude_client._trim_history()
        assert len(claude_client._conversation) == 10  # 5 * 2

    def test_trim_history_no_op_when_within_limit(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        claude_client._max_history = 50
        claude_client._conversation = [{"role": "user", "content": "hi"}] * 5
        claude_client._trim_history()
        assert len(claude_client._conversation) == 5

    def test_default_max_history_is_20(self, claude_client: ClaudeClient) -> None:
        assert claude_client._max_history == 20

    def test_custom_max_history(
        self,
        claude_client_custom_tokens: ClaudeClient,
    ) -> None:
        assert claude_client_custom_tokens._max_history == 10

    def test_aggressive_trim_with_small_history(
        self,
        claude_client_custom_tokens: ClaudeClient,
    ) -> None:
        """With max_history=10, trim at >20 messages."""
        client = claude_client_custom_tokens
        client._conversation = [
            {"role": "user", "content": f"msg {i}"} for i in range(30)
        ]
        client._trim_history()
        assert len(client._conversation) == 20  # 10 * 2


# ---------------------------------------------------------------------------
# Token budget configuration
# ---------------------------------------------------------------------------


class TestTokenBudgetConfig:
    """Test that ClaudeClient stores and uses token budgets correctly."""

    def test_default_max_tokens(self, claude_client: ClaudeClient) -> None:
        assert claude_client._max_tokens == 1024

    def test_default_briefing_tokens(self, claude_client: ClaudeClient) -> None:
        assert claude_client._max_tokens_briefing == 2048

    def test_custom_max_tokens(
        self,
        claude_client_custom_tokens: ClaudeClient,
    ) -> None:
        assert claude_client_custom_tokens._max_tokens == 512

    def test_custom_briefing_tokens(
        self,
        claude_client_custom_tokens: ClaudeClient,
    ) -> None:
        assert claude_client_custom_tokens._max_tokens_briefing == 1500


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
    async def test_execute_tool_catches_exceptions(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.get_sim_state",
            side_effect=RuntimeError("sim offline"),
        ):
            result = await claude_client._execute_tool(
                "get_sim_state", {}, SimState()
            )
            assert "error" in result
            assert "sim offline" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_get_sim_state(
        self,
        claude_client: ClaudeClient,
        mock_sim_client: MagicMock,
    ) -> None:
        mock_state = SimState(aircraft="Test Aircraft")
        mock_sim_client.get_state = AsyncMock(return_value=mock_state)
        with patch(
            "orchestrator.claude_client.get_sim_state",
            new_callable=AsyncMock,
            return_value={"aircraft": "Test Aircraft"},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "get_sim_state", {}, SimState()
            )
            mock_fn.assert_awaited_once()
            assert result["aircraft"] == "Test Aircraft"

    @pytest.mark.asyncio
    async def test_execute_lookup_airport(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.lookup_airport",
            new_callable=AsyncMock,
            return_value={"identifier": "KJFK"},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "lookup_airport", {"identifier": "KJFK"}, SimState()
            )
            mock_fn.assert_awaited_once_with("KJFK")
            assert result["identifier"] == "KJFK"

    @pytest.mark.asyncio
    async def test_execute_search_manual(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = SimState(aircraft="Cessna 172")
        with patch(
            "orchestrator.claude_client.search_manual",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "search_manual", {"query": "V-speeds"}, state
            )
            mock_fn.assert_awaited_once()
            call_kwargs = mock_fn.call_args
            assert call_kwargs[0][0] == "V-speeds"
            assert call_kwargs[1]["aircraft_type"] == "Cessna 172"

    @pytest.mark.asyncio
    async def test_execute_get_checklist(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.get_checklist",
            new_callable=AsyncMock,
            return_value={"phase": "PREFLIGHT"},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "get_checklist", {"phase": "PREFLIGHT"}, SimState()
            )
            mock_fn.assert_awaited_once()
            assert result["phase"] == "PREFLIGHT"

    @pytest.mark.asyncio
    async def test_execute_create_flight_plan(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.create_flight_plan",
            new_callable=AsyncMock,
            return_value={"status": "draft"},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "create_flight_plan",
                {
                    "departure": "KJFK",
                    "destination": "KLAX",
                    "altitude": 35000,
                    "route": "J80",
                },
                SimState(),
            )
            mock_fn.assert_awaited_once()
            assert result["status"] == "draft"
