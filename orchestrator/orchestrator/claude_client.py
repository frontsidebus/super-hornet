"""Wrapper around the Anthropic API with Super Hornet persona and tool definitions."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic

from .context_store import ContextStore
from .game_state import GameActivity, GameState
from .tools import (
    get_game_state,
    get_procedure,
    get_ship_status,
    get_skill,
    lookup_commodity,
    plan_trade_route,
    search_knowledge,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Super Hornet persona — prefer the rich markdown version from disk.
# ---------------------------------------------------------------------------

_HORNET_SYSTEM_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "prompts" / "hornet_system.md"
)

_INLINE_PERSONA = """\
You are Super Hornet, an advanced AI wingman and operations officer for Star Citizen. You are \
the pilot's co-pilot, navigator, tactical advisor, and ship systems specialist.

- **Address**: Always call the pilot "Commander" or by their callsign.
- **Tone**: Professional but not stiff. Veteran military advisor with dry humor.
- **Combat**: Terse, tactical, callout-style. No filler, no jokes.
- **Cruise/QT**: Conversational, good for planning and briefing.
- **Knowledge**: Deep expertise in all Star Citizen ships, systems, trading, mining, combat, \
navigation, and game mechanics.
- **Limitations**: State clearly when uncertain about game state. Never hallucinate telemetry.

Current game context will be injected below. Use it to make your responses situationally aware.
"""

# ---------------------------------------------------------------------------
# Response pacing directives appended to every system prompt.
# ---------------------------------------------------------------------------

_RESPONSE_PACING = """\

--- RESPONSE RULES ---
Keep responses concise and tactical. In the verse, brevity keeps you alive.
- For routine comms, acknowledgments, and simple questions: 1-3 sentences MAX.
- For procedures and checklists: present items in groups of 3-5, then wait.
- For briefings and trade plans: be thorough but structured — use bullet points, not prose.
- NEVER ramble. If you catch yourself writing a paragraph, stop and restructure.
- After asking a question, STOP. Do not answer your own question.
- After giving a key callout, STOP. Let the Commander respond.
- Use Star Citizen terminology naturally (QT, SCM, aUEC, MobiGlas, etc.).
"""

# ---------------------------------------------------------------------------
# Activity-specific response style directives.
# ---------------------------------------------------------------------------

_ACTIVITY_STYLE: dict[GameActivity, str] = {
    GameActivity.IDLE: (
        "Activity: IDLE. Relaxed tone, moderate length. Good time for banter, "
        "loadout discussion, and planning."
    ),
    GameActivity.ON_FOOT: (
        "Activity: ON FOOT. Moderate length. Can discuss missions, locations, gear."
    ),
    GameActivity.SHIP_IDLE: (
        "Activity: SHIP IDLE. Professional, moderate. Good time for pre-flight and systems check."
    ),
    GameActivity.SHIP_FLIGHT: (
        "Activity: SHIP FLIGHT. Professional, concise. 1-2 sentences unless briefing."
    ),
    GameActivity.QUANTUM_TRAVEL: (
        "Activity: QUANTUM TRAVEL. Conversational, can be more detailed. "
        "Good time to plan, brief, or discuss strategy."
    ),
    GameActivity.COMBAT: (
        "Activity: COMBAT. ULTRA-BRIEF. Tactical callouts only. No humor. No filler. "
        "Lead with the most critical information."
    ),
    GameActivity.MINING: (
        "Activity: MINING. Technical and precise. Focus on rock composition, "
        "laser power, and extraction efficiency."
    ),
    GameActivity.SALVAGE: (
        "Activity: SALVAGE. Technical, moderate. Focus on component identification "
        "and extraction technique."
    ),
    GameActivity.TRADING: (
        "Activity: TRADING. Analytical. Focus on commodity prices, margins, "
        "and route optimization."
    ),
    GameActivity.LANDING: (
        "Activity: LANDING. Concise callouts. Landing pad identification, "
        "speed, approach guidance."
    ),
    GameActivity.EVA: (
        "Activity: EVA. Moderate length. Focus on orientation and objective."
    ),
    GameActivity.ENGINEERING: (
        "Activity: ENGINEERING. Technical, moderate. Focus on power distribution, "
        "component health, and repair priorities."
    ),
}

# ---------------------------------------------------------------------------
# Stop sequences for natural conversation breaks.
# ---------------------------------------------------------------------------

STOP_SEQUENCES: list[str] = ["\nover.", "\nOver.", "\nover", "\nOver"]


def _load_hornet_persona() -> str:
    """Return the full Super Hornet system prompt, preferring on-disk markdown."""
    if _HORNET_SYSTEM_PATH.exists():
        try:
            return _HORNET_SYSTEM_PATH.read_text(encoding="utf-8")
        except Exception:
            logger.warning(
                "Failed to read %s; falling back to inline persona",
                _HORNET_SYSTEM_PATH,
            )
    return _INLINE_PERSONA


HORNET_PERSONA: str = _load_hornet_persona()

# ---------------------------------------------------------------------------
# Query classification for response budgeting.
# ---------------------------------------------------------------------------

_BRIEFING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(plan|route|trade\s*route|loadout|build|guide)\b", re.I),
    re.compile(r"\b(walk\s+me\s+through|explain|teach|how\s+does|how\s+do\s+I)\b", re.I),
    re.compile(r"\b(create|build|make)\s+(a\s+)?(trade\s*route|route|plan|loadout)\b", re.I),
    re.compile(r"\b(brief|briefing|checklist|procedure)\b", re.I),
]

_SHORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(roger|copy|got it|ok|okay|thanks|thank you)\b", re.I),
    re.compile(r"^(yes|no|yep|nope|yeah|nah)\b", re.I),
    re.compile(
        r"\b(what\s*'?s?\s+(my|our|the)\s+(shields?|fuel|speed|location|crime\s*stat))\b",
        re.I,
    ),
    re.compile(r"\b(how\s+(much|far|long|many))\b", re.I),
    re.compile(r"\b(where\s+(am\s+I|are\s+we))\b", re.I),
]


def classify_query(user_message: str) -> str:
    """Classify a message as 'short', 'briefing', or 'normal'."""
    text = user_message.strip()
    for pat in _SHORT_PATTERNS:
        if pat.search(text):
            return "short"
    for pat in _BRIEFING_PATTERNS:
        if pat.search(text):
            return "briefing"
    return "normal"


def max_tokens_for_query(
    query_type: str,
    default_max: int = 1024,
    briefing_max: int = 2048,
) -> int:
    """Return the appropriate max_tokens budget for a query type."""
    if query_type == "short":
        return min(256, default_max)
    if query_type == "briefing":
        return briefing_max
    return default_max


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_game_state",
        "description": (
            "Retrieve the current game state including ship status, player location, "
            "combat state, and recent log events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "lookup_commodity",
        "description": (
            "Look up commodity prices and availability from the UEX Corp database. "
            "Can filter by commodity name and/or location."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commodity": {
                    "type": "string",
                    "description": "Commodity name or code (e.g., 'Laranite', 'Agricium')",
                },
                "location": {
                    "type": "string",
                    "description": "Optional location filter (e.g., 'Lorville', 'New Babbage')",
                    "default": "",
                },
            },
            "required": ["commodity"],
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "Search the Star Citizen knowledge base for ship specs, game mechanics, "
            "lore, procedures, or any game-related information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query describing what to look up",
                },
                "ship_name": {
                    "type": "string",
                    "description": "Optional ship name to filter results",
                    "default": "",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_ship_status",
        "description": (
            "Get detailed ship status including shields, hull, fuel levels, "
            "power state, weapons, and quantum drive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "plan_trade_route",
        "description": (
            "Plan an optimal trade route between two locations. Returns commodity "
            "recommendations, expected profit, and route details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "Starting location (e.g., 'Lorville', 'Area18')",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination location (e.g., 'New Babbage', 'Orison')",
                },
                "cargo_scu": {
                    "type": "integer",
                    "description": "Available cargo capacity in SCU",
                    "default": 100,
                },
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "get_procedure",
        "description": (
            "Get the procedure or checklist for a specific game activity. "
            "Returns step-by-step instructions with keybinds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity": {
                    "type": "string",
                    "description": (
                        "Game activity (SHIP_IDLE, SHIP_FLIGHT, QUANTUM_TRAVEL, COMBAT, "
                        "MINING, SALVAGE, TRADING, LANDING, ENGINEERING)"
                    ),
                },
            },
            "required": ["activity"],
        },
    },
    {
        "name": "get_skill",
        "description": (
            "Search the skill library for a learned action sequence. Skills are "
            "verified keystroke sequences for common operations like quantum travel, "
            "ship power-up, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you want to do (e.g., 'quantum travel', 'power on ship')",
                },
            },
            "required": ["query"],
        },
    },
]


class ClaudeClient:
    """Manages conversations with Claude using the Super Hornet persona."""

    def __init__(
        self,
        api_key: str,
        model: str,
        game_client: Any,
        context_store: ContextStore,
        skill_library: Any = None,
        uex_client: Any = None,
        input_simulator: Any = None,
        max_tokens: int = 1024,
        max_tokens_briefing: int = 2048,
        max_history: int = 20,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._game_client = game_client
        self._context_store = context_store
        self._skill_library = skill_library
        self._uex_client = uex_client
        self._input_simulator = input_simulator
        self._conversation: list[dict[str, Any]] = []
        self._max_history = max_history
        self._max_tokens = max_tokens
        self._max_tokens_briefing = max_tokens_briefing

    def _build_system_prompt(
        self,
        game_state: GameState,
        context_docs: list[dict[str, Any]],
    ) -> str:
        parts = [HORNET_PERSONA]

        # Activity-aware response style
        activity = game_state.activity
        if activity in _ACTIVITY_STYLE:
            parts.append(f"\n--- CURRENT RESPONSE STYLE ---\n{_ACTIVITY_STYLE[activity]}")

        # Current game state context
        parts.append(f"\n--- CURRENT GAME STATE ---\n{game_state.state_summary()}")

        if game_state.player.in_ship and game_state.ship.name:
            parts.append(f"Ship: {game_state.ship.name}")
            if game_state.ship.weapons_armed:
                parts.append("Weapons: ARMED")
            if game_state.ship.quantum_drive_active:
                parts.append("Quantum Drive: ACTIVE")

        if game_state.player.crime_stat > 0:
            parts.append(f"WARNING: CrimeStat {game_state.player.crime_stat} active")

        if game_state.combat.under_attack:
            parts.append("ALERT: Under attack!")
            if game_state.combat.hostile_count > 0:
                parts.append(f"Hostile contacts: {game_state.combat.hostile_count}")

        if context_docs:
            parts.append("\n--- RELEVANT REFERENCE MATERIAL ---")
            for doc in context_docs[:3]:
                source = doc.get("metadata", {}).get("source", "unknown")
                parts.append(f"[{source}]\n{doc['content'][:500]}")

        parts.append(_RESPONSE_PACING)

        return "\n".join(parts)

    async def chat(
        self,
        user_message: str,
        game_state: GameState | None = None,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        """Send a message and yield streamed response text chunks.

        Handles tool use loops internally, yielding text as it arrives.
        """
        if game_state is None:
            try:
                game_state = await self._game_client.get_state()
            except Exception:
                game_state = GameState()

        context_docs = await self._context_store.get_relevant_context(game_state)
        system = self._build_system_prompt(game_state, context_docs)

        # Classify the query to set an appropriate token budget
        query_type = classify_query(user_message)
        effective_max_tokens = max_tokens_for_query(
            query_type,
            default_max=self._max_tokens,
            briefing_max=self._max_tokens_briefing,
        )

        # Build user message content
        content: list[dict[str, Any]] = []
        if image_base64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_base64,
                },
            })
        content.append({"type": "text", "text": user_message})

        self._conversation.append({"role": "user", "content": content})
        self._trim_history()

        # Agentic loop: keep going while Claude wants to use tools
        while True:
            collected_text = ""
            tool_use_blocks: list[dict[str, Any]] = []
            current_tool_input = ""
            current_tool_id = ""
            current_tool_name = ""
            stop_reason = None

            async with self._client.messages.stream(
                model=self._model,
                max_tokens=effective_max_tokens,
                system=system,
                messages=self._conversation,
                tools=TOOL_DEFINITIONS,
                stop_sequences=STOP_SEQUENCES,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            current_tool_id = event.content_block.id
                            current_tool_name = event.content_block.name
                            current_tool_input = ""
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            collected_text += event.delta.text
                            yield event.delta.text
                        elif event.delta.type == "input_json_delta":
                            current_tool_input += event.delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool_name:
                            tool_input = (
                                json.loads(current_tool_input)
                                if current_tool_input
                                else {}
                            )
                            tool_use_blocks.append({
                                "id": current_tool_id,
                                "name": current_tool_name,
                                "input": tool_input,
                            })
                            current_tool_name = ""
                    elif event.type == "message_delta":
                        stop_reason = event.delta.stop_reason

            # Record assistant turn
            assistant_content: list[dict[str, Any]] = []
            if collected_text:
                assistant_content.append({"type": "text", "text": collected_text})
            for tb in tool_use_blocks:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tb["id"],
                    "name": tb["name"],
                    "input": tb["input"],
                })
            self._conversation.append({"role": "assistant", "content": assistant_content})

            if stop_reason != "tool_use" or not tool_use_blocks:
                break

            # Execute tools and feed results back
            tool_results: list[dict[str, Any]] = []
            for tb in tool_use_blocks:
                result = await self._execute_tool(tb["name"], tb["input"], game_state)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb["id"],
                    "content": json.dumps(result),
                })
            self._conversation.append({"role": "user", "content": tool_results})

    async def _execute_tool(
        self, name: str, args: dict[str, Any], game_state: GameState
    ) -> Any:
        logger.info("Executing tool: %s(%s)", name, args)
        try:
            if name == "get_game_state":
                return await get_game_state(self._game_client)
            elif name == "lookup_commodity":
                return await lookup_commodity(
                    args["commodity"],
                    self._uex_client,
                    location=args.get("location", ""),
                )
            elif name == "search_knowledge":
                return await search_knowledge(
                    args["query"],
                    self._context_store,
                    ship_name=args.get("ship_name", ""),
                )
            elif name == "get_ship_status":
                return await get_ship_status(self._game_client)
            elif name == "plan_trade_route":
                return await plan_trade_route(
                    args["origin"],
                    args["destination"],
                    args.get("cargo_scu", 100),
                    self._uex_client,
                )
            elif name == "get_procedure":
                return await get_procedure(
                    args["activity"],
                    self._context_store,
                    ship_name=game_state.ship.name,
                )
            elif name == "get_skill":
                return await get_skill(args["query"], self._skill_library)
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.exception("Tool execution failed: %s", name)
            return {"error": str(e)}

    def clear_history(self) -> None:
        self._conversation.clear()

    def _trim_history(self) -> None:
        if len(self._conversation) > self._max_history * 2:
            self._conversation = self._conversation[-(self._max_history * 2) :]
