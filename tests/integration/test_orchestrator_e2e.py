"""Lightweight end-to-end tests for the MERLIN Orchestrator.

These tests wire up the orchestrator with a mock SimConnect server and a
mocked Claude API to verify the full request/response flow without
requiring real external services (except a local ChromaDB directory).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from orchestrator.claude_client import ClaudeClient
from orchestrator.config import Settings
from orchestrator.context_store import ContextStore
from orchestrator.main import Orchestrator
from orchestrator.sim_client import SimConnectClient, SimState

from .conftest import MockSimConnectServer

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_settings(tmp_path: Path, mock_simconnect_server: MockSimConnectServer) -> Settings:
    """Settings that point to the mock server and temp storage."""
    return Settings(
        anthropic_api_key="sk-ant-test-fake-key",
        simconnect_bridge_url=mock_simconnect_server.url,
        chromadb_path=str(tmp_path / "chromadb"),
        whisper_url="http://localhost:9999",  # not used in text mode
        claude_model="claude-sonnet-4-20250514",
        screen_capture_enabled=False,
    )


def _make_mock_stream_response(text: str) -> AsyncMock:
    """Build a mock that simulates the Anthropic streaming messages API.

    Returns an async context manager whose stream yields content_block_start,
    content_block_delta (text), content_block_stop, and message_delta events.
    """

    class FakeEvent:
        def __init__(self, etype: str, **kwargs: Any) -> None:
            self.type = etype
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeDelta:
        def __init__(self, dtype: str, **kwargs: Any) -> None:
            self.type = dtype
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeContentBlock:
        def __init__(self, btype: str, **kwargs: Any) -> None:
            self.type = btype
            for k, v in kwargs.items():
                setattr(self, k, v)

    events = [
        FakeEvent(
            "content_block_start",
            content_block=FakeContentBlock("text"),
        ),
        FakeEvent(
            "content_block_delta",
            delta=FakeDelta("text_delta", text=text),
        ),
        FakeEvent("content_block_stop"),
        FakeEvent(
            "message_delta",
            delta=FakeDelta("message_delta", stop_reason="end_turn"),
        ),
    ]

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if events:
                return events.pop(0)
            raise StopAsyncIteration

    return FakeStream()


# ---------------------------------------------------------------------------
# ClaudeClient integration (mocked Anthropic API)
# ---------------------------------------------------------------------------


class TestClaudeClientChat:
    async def test_chat_returns_streamed_text(
        self, mock_simconnect_server: MockSimConnectServer, tmp_path: Path
    ) -> None:
        """chat() should yield text chunks from the Claude API stream."""
        sim_client = SimConnectClient(mock_simconnect_server.url)
        context_store = ContextStore(persist_path=str(tmp_path / "chroma"))

        claude = ClaudeClient(
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
            sim_client=sim_client,
            context_store=context_store,
        )

        expected_text = "Roger, Captain. Altitude is 5500 feet, looking good."
        mock_stream = _make_mock_stream_response(expected_text)

        with patch.object(claude._client.messages, "stream", return_value=mock_stream):
            chunks: list[str] = []
            async for chunk in claude.chat("How's our altitude?", sim_state=SimState()):
                chunks.append(chunk)

        full_response = "".join(chunks)
        assert full_response == expected_text

    async def test_chat_builds_system_prompt_with_sim_state(
        self, mock_simconnect_server: MockSimConnectServer, tmp_path: Path
    ) -> None:
        """The system prompt should include telemetry from sim state."""
        sim_client = SimConnectClient(mock_simconnect_server.url)
        context_store = ContextStore(persist_path=str(tmp_path / "chroma"))

        claude = ClaudeClient(
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
            sim_client=sim_client,
            context_store=context_store,
        )

        sim_state = SimState(
            aircraft_title="Cessna 172S Skyhawk",
            position={"altitude": 8000},
            speeds={"indicated": 120, "vertical_speed": 0},
            attitude={"heading": 270},
        )

        system_prompt = claude._build_system_prompt(sim_state, [])
        assert "MERLIN" in system_prompt
        assert "Cessna 172S Skyhawk" in system_prompt
        assert "CURRENT FLIGHT STATE" in system_prompt

    async def test_clear_history(
        self, mock_simconnect_server: MockSimConnectServer, tmp_path: Path
    ) -> None:
        sim_client = SimConnectClient(mock_simconnect_server.url)
        context_store = ContextStore(persist_path=str(tmp_path / "chroma"))

        claude = ClaudeClient(
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
            sim_client=sim_client,
            context_store=context_store,
        )

        # Manually add some conversation history
        claude._conversation.append({"role": "user", "content": "test"})
        claude._conversation.append({"role": "assistant", "content": "response"})
        assert len(claude._conversation) == 2

        claude.clear_history()
        assert len(claude._conversation) == 0


# ---------------------------------------------------------------------------
# ClaudeClient tool use loop (mocked)
# ---------------------------------------------------------------------------


class TestClaudeToolLoop:
    async def test_tool_use_then_text_response(
        self, mock_simconnect_server: MockSimConnectServer, tmp_path: Path
    ) -> None:
        """Simulate Claude requesting a tool, then producing a final text response."""
        sim_client = SimConnectClient(mock_simconnect_server.url)
        await sim_client.connect()

        context_store = ContextStore(persist_path=str(tmp_path / "chroma"))

        claude = ClaudeClient(
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
            sim_client=sim_client,
            context_store=context_store,
        )

        class FakeEvent:
            def __init__(self, etype, **kw):
                self.type = etype
                for k, v in kw.items():
                    setattr(self, k, v)

        class FakeDelta:
            def __init__(self, dtype, **kw):
                self.type = dtype
                for k, v in kw.items():
                    setattr(self, k, v)

        class FakeBlock:
            def __init__(self, btype, **kw):
                self.type = btype
                for k, v in kw.items():
                    setattr(self, k, v)

        # First call: Claude requests get_checklist tool
        tool_events = [
            FakeEvent("content_block_start", content_block=FakeBlock("tool_use", id="t1", name="get_checklist")),
            FakeEvent("content_block_delta", delta=FakeDelta("input_json_delta", partial_json='{"phase":')),
            FakeEvent("content_block_delta", delta=FakeDelta("input_json_delta", partial_json=' "CRUISE"}')),
            FakeEvent("content_block_stop"),
            FakeEvent("message_delta", delta=FakeDelta("message_delta", stop_reason="tool_use")),
        ]

        # Second call: Claude produces text
        text_events = [
            FakeEvent("content_block_start", content_block=FakeBlock("text")),
            FakeEvent("content_block_delta", delta=FakeDelta("text_delta", text="Here's your cruise checklist, Captain.")),
            FakeEvent("content_block_stop"),
            FakeEvent("message_delta", delta=FakeDelta("message_delta", stop_reason="end_turn")),
        ]

        call_count = 0

        def make_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            evts = tool_events if call_count == 1 else text_events

            class Stream:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    pass
                def __aiter__(self):
                    return self._gen()
                async def _gen(self):
                    for e in evts:
                        yield e

            return Stream()

        with patch.object(claude._client.messages, "stream", side_effect=make_stream):
            chunks = []
            async for chunk in claude.chat("What's my cruise checklist?", sim_state=SimState()):
                chunks.append(chunk)

        full = "".join(chunks)
        assert "cruise checklist" in full.lower()
        assert call_count == 2  # tool call + final response

        await sim_client.disconnect()


# ---------------------------------------------------------------------------
# Orchestrator slash commands
# ---------------------------------------------------------------------------


class TestOrchestratorCommands:
    async def test_clear_command(
        self, tmp_settings: Settings, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """The /clear command should reset Claude conversation history."""
        orchestrator = Orchestrator(tmp_settings)

        # Manually inject some history
        orchestrator._claude._conversation.append({"role": "user", "content": "hi"})
        assert len(orchestrator._claude._conversation) == 1

        result = await orchestrator._handle_command("/clear")
        assert result is True
        assert len(orchestrator._claude._conversation) == 0

    async def test_status_command_without_connection(
        self, tmp_settings: Settings
    ) -> None:
        """/status should handle the case where SimConnect is not connected."""
        orchestrator = Orchestrator(tmp_settings)
        # Don't connect -- just run the command
        result = await orchestrator._handle_command("/status")
        assert result is True

    async def test_quit_command(self, tmp_settings: Settings) -> None:
        orchestrator = Orchestrator(tmp_settings)
        assert orchestrator._running is False

        orchestrator._running = True
        result = await orchestrator._handle_command("/quit")
        assert result is True
        assert orchestrator._running is False

    async def test_unknown_command(self, tmp_settings: Settings) -> None:
        orchestrator = Orchestrator(tmp_settings)
        result = await orchestrator._handle_command("/foobar")
        assert result is True  # still "handled" (prints error message)


# ---------------------------------------------------------------------------
# Full orchestrator text input flow (mocked Claude)
# ---------------------------------------------------------------------------


class TestOrchestratorTextFlow:
    async def test_text_input_produces_response(
        self,
        tmp_settings: Settings,
        mock_simconnect_server: MockSimConnectServer,
    ) -> None:
        """Sending text input should build context, call Claude, return a response."""
        orchestrator = Orchestrator(tmp_settings)

        expected = "Good afternoon, Captain. Systems nominal."
        mock_stream = _make_mock_stream_response(expected)

        with patch.object(
            orchestrator._claude._client.messages, "stream", return_value=mock_stream
        ):
            chunks = []
            async for chunk in orchestrator._claude.chat(
                "How are systems looking?", sim_state=SimState()
            ):
                chunks.append(chunk)

        assert "".join(chunks) == expected
