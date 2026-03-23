"""Tests for orchestrator.claude_client — system prompt, token budgets, query classification."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.claude_client import (
    HORNET_PERSONA,
    STOP_SEQUENCES,
    TOOL_DEFINITIONS,
    ClaudeClient,
    classify_query,
    max_tokens_for_query,
)
from orchestrator.game_state import (
    GameActivity,
    GameState,
    PlayerStatus,
    ShipStatus,
    CombatState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_game_client() -> MagicMock:
    client = MagicMock()
    client.get_state = AsyncMock(return_value=GameState())
    return client


@pytest.fixture
def mock_context_store() -> MagicMock:
    store = MagicMock()
    store.get_relevant_context = AsyncMock(return_value=[])
    store.query = AsyncMock(return_value=[])
    return store


@pytest.fixture
def claude_client(mock_game_client: MagicMock, mock_context_store: MagicMock) -> ClaudeClient:
    with patch("orchestrator.claude_client.anthropic.AsyncAnthropic"):
        return ClaudeClient(
            api_key="sk-ant-test",
            model="claude-sonnet-4-20250514",
            game_client=mock_game_client,
            context_store=mock_context_store,
        )


@pytest.fixture
def claude_client_custom_tokens(
    mock_game_client: MagicMock,
    mock_context_store: MagicMock,
) -> ClaudeClient:
    """ClaudeClient with custom token limits for testing."""
    with patch("orchestrator.claude_client.anthropic.AsyncAnthropic"):
        return ClaudeClient(
            api_key="sk-ant-test",
            model="claude-sonnet-4-20250514",
            game_client=mock_game_client,
            context_store=mock_context_store,
            max_tokens=512,
            max_tokens_briefing=1500,
            max_history=10,
        )


@pytest.fixture
def game_state_combat() -> GameState:
    """Game state during active combat."""
    return GameState(
        activity=GameActivity.COMBAT,
        player=PlayerStatus(
            in_ship=True,
            location_system="Stanton",
            location_body="Crusader",
        ),
        ship=ShipStatus(
            name="Super Hornet",
            shields_front=60.0,
            shields_rear=40.0,
            hull_percent=85.0,
            weapons_armed=True,
        ),
        combat=CombatState(
            under_attack=True,
            hostile_count=2,
            target_name="Buccaneer",
            target_distance_km=1.2,
        ),
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

    def test_get_game_state_has_no_required_params(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_game_state")
        assert tool["input_schema"]["required"] == []

    def test_lookup_commodity_requires_commodity(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "lookup_commodity")
        assert "commodity" in tool["input_schema"]["required"]

    def test_search_knowledge_requires_query(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_knowledge")
        assert "query" in tool["input_schema"]["required"]

    def test_get_procedure_requires_activity(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_procedure")
        assert "activity" in tool["input_schema"]["required"]

    def test_plan_trade_route_requires_origin_and_destination(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "plan_trade_route")
        required = tool["input_schema"]["required"]
        assert "origin" in required
        assert "destination" in required

    def test_seven_tools_defined(self) -> None:
        assert len(TOOL_DEFINITIONS) == 7

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
            ("copy", "short"),
            ("got it", "short"),
            ("ok", "short"),
            ("yes", "short"),
            ("no", "short"),
            ("What's my shields?", "short"),
            ("Where am I?", "short"),
            ("How much fuel?", "short"),
            ("How far to destination?", "short"),
        ],
    )
    def test_short_queries(self, message: str, expected: str) -> None:
        assert classify_query(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Plan a trade route", "briefing"),
            ("Walk me through mining", "briefing"),
            ("Explain quantum travel", "briefing"),
            ("Create a loadout", "briefing"),
            ("Build a route to New Babbage", "briefing"),
        ],
    )
    def test_briefing_queries(self, message: str, expected: str) -> None:
        assert classify_query(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("What ship should I use?", "normal"),
            ("Should I engage?", "normal"),
            ("Any hostiles nearby?", "normal"),
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
    """Test _build_system_prompt with various GameState and context combinations."""

    def test_prompt_contains_persona(self, claude_client: ClaudeClient) -> None:
        prompt = claude_client._build_system_prompt(GameState(), [])
        assert "Super Hornet" in prompt or "Commander" in prompt

    def test_prompt_contains_state_summary(
        self,
        claude_client: ClaudeClient,
        game_state_combat: GameState,
    ) -> None:
        prompt = claude_client._build_system_prompt(game_state_combat, [])
        assert "COMBAT" in prompt
        assert "UNDER ATTACK" in prompt

    def test_prompt_contains_ship_name(self, claude_client: ClaudeClient) -> None:
        state = GameState(
            player=PlayerStatus(in_ship=True),
            ship=ShipStatus(name="Gladius"),
        )
        prompt = claude_client._build_system_prompt(state, [])
        assert "Gladius" in prompt

    def test_prompt_includes_crimestat_warning(self, claude_client: ClaudeClient) -> None:
        state = GameState(player=PlayerStatus(crime_stat=3))
        prompt = claude_client._build_system_prompt(state, [])
        assert "CrimeStat" in prompt or "crime" in prompt.lower()

    def test_prompt_includes_activity_style_for_combat(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = GameState(activity=GameActivity.COMBAT)
        prompt = claude_client._build_system_prompt(state, [])
        assert "ULTRA-BRIEF" in prompt

    def test_prompt_includes_activity_style_for_quantum(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = GameState(activity=GameActivity.QUANTUM_TRAVEL)
        prompt = claude_client._build_system_prompt(state, [])
        assert "Conversational" in prompt

    def test_prompt_includes_context_docs(self, claude_client: ClaudeClient) -> None:
        docs = [
            {
                "content": "Quantum drive spool procedure for Anvil ships...",
                "metadata": {"source": "sc_manual.pdf"},
            },
            {
                "content": "Shield management during combat...",
                "metadata": {"source": "combat_guide.pdf"},
            },
        ]
        prompt = claude_client._build_system_prompt(GameState(), docs)
        assert "RELEVANT REFERENCE MATERIAL" in prompt
        assert "sc_manual.pdf" in prompt
        assert "Quantum drive spool" in prompt

    def test_prompt_limits_context_to_three_docs(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        docs = [
            {"content": f"Doc {i}", "metadata": {"source": f"src{i}.pdf"}}
            for i in range(5)
        ]
        prompt = claude_client._build_system_prompt(GameState(), docs)
        assert "src0.pdf" in prompt
        assert "src2.pdf" in prompt
        assert "src3.pdf" not in prompt

    def test_prompt_truncates_long_content(self, claude_client: ClaudeClient) -> None:
        docs = [{"content": "X" * 1000, "metadata": {"source": "big.pdf"}}]
        prompt = claude_client._build_system_prompt(GameState(), docs)
        # Content should be truncated to 500 chars
        assert "X" * 500 in prompt
        assert "X" * 501 not in prompt

    def test_prompt_includes_response_pacing_rules(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        prompt = claude_client._build_system_prompt(GameState(), [])
        assert "RESPONSE RULES" in prompt
        assert "brevity" in prompt.lower()
        assert "STOP" in prompt


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
        result = await claude_client._execute_tool("nonexistent_tool", {}, GameState())
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_catches_exceptions(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.get_game_state",
            side_effect=RuntimeError("game offline"),
        ):
            result = await claude_client._execute_tool(
                "get_game_state", {}, GameState()
            )
            assert "error" in result
            assert "game offline" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_get_game_state(
        self,
        claude_client: ClaudeClient,
        mock_game_client: MagicMock,
    ) -> None:
        mock_state = GameState(
            activity=GameActivity.SHIP_FLIGHT,
            ship=ShipStatus(name="Super Hornet"),
        )
        mock_game_client.get_state = AsyncMock(return_value=mock_state)
        with patch(
            "orchestrator.claude_client.get_game_state",
            new_callable=AsyncMock,
            return_value={"activity": "SHIP_FLIGHT", "ship": {"name": "Super Hornet"}},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "get_game_state", {}, GameState()
            )
            mock_fn.assert_awaited_once()
            assert result["activity"] == "SHIP_FLIGHT"

    @pytest.mark.asyncio
    async def test_execute_lookup_commodity(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.lookup_commodity",
            new_callable=AsyncMock,
            return_value={"commodity": "Laranite", "price": 2750},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "lookup_commodity", {"commodity": "Laranite"}, GameState()
            )
            mock_fn.assert_awaited_once()
            assert result["commodity"] == "Laranite"

    @pytest.mark.asyncio
    async def test_execute_search_knowledge(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        state = GameState(ship=ShipStatus(name="Super Hornet"))
        with patch(
            "orchestrator.claude_client.search_knowledge",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "search_knowledge", {"query": "shield management"}, state
            )
            mock_fn.assert_awaited_once()
            call_kwargs = mock_fn.call_args
            assert call_kwargs[0][0] == "shield management"

    @pytest.mark.asyncio
    async def test_execute_get_procedure(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.get_procedure",
            new_callable=AsyncMock,
            return_value={"activity": "COMBAT"},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "get_procedure", {"activity": "COMBAT"}, GameState()
            )
            mock_fn.assert_awaited_once()
            assert result["activity"] == "COMBAT"

    @pytest.mark.asyncio
    async def test_execute_plan_trade_route(
        self,
        claude_client: ClaudeClient,
    ) -> None:
        with patch(
            "orchestrator.claude_client.plan_trade_route",
            new_callable=AsyncMock,
            return_value={"status": "planned", "profit": 15000},
        ) as mock_fn:
            result = await claude_client._execute_tool(
                "plan_trade_route",
                {
                    "origin": "Lorville",
                    "destination": "New Babbage",
                    "cargo_scu": 100,
                },
                GameState(),
            )
            mock_fn.assert_awaited_once()
            assert result["status"] == "planned"
