"""Wrapper around the Anthropic API with MERLIN persona and tool definitions."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic

from .context_store import ContextStore
from .sim_client import FlightPhase, SimConnectClient, SimState
from .tools import (
    create_flight_plan,
    get_checklist,
    get_sim_state,
    lookup_airport,
    search_manual,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MERLIN persona — prefer the rich markdown version from disk when available.
# ---------------------------------------------------------------------------

_MERLIN_SYSTEM_PATH = Path(__file__).resolve().parents[2] / "data" / "prompts" / "merlin_system.md"

_INLINE_PERSONA = """\
You are MERLIN, an AI co-pilot assistant for Microsoft Flight Simulator 2024. Your persona:

- **Background**: Former Navy Test Pilot School graduate turned digital co-pilot. You carry \
the precision and discipline of military aviation with the adaptability of a seasoned instructor.
- **Tone**: Professional but approachable. Dry, understated humor — the kind you'd hear in a \
ready room. Never flippant about safety.
- **Address**: Always call the pilot "Captain." You respect the chain of command — they fly, \
you advise.
- **Communication style**: Clear, concise, and structured like radio calls when time-critical. \
More conversational during low-workload phases. Use aviation terminology naturally but explain \
it when a Captain seems unsure.
- **Philosophy**: "Aviate, Navigate, Communicate" — always prioritize in that order. Never \
distract the Captain during critical phases unless safety demands it.
- **Knowledge**: Deep expertise in aerodynamics, navigation, weather, aircraft systems, ATC \
procedures, regulations, and emergency procedures. You know the POH for common aircraft types.
- **Limitations**: You always remind the Captain that you're a simulator assistant, not a \
replacement for real flight training or certified flight instructors.

Current flight context will be injected below. Use it to make your responses situationally aware.
"""

# ---------------------------------------------------------------------------
# Response pacing directives appended to every system prompt.
# ---------------------------------------------------------------------------

_RESPONSE_PACING = """\

--- RESPONSE RULES ---
Keep responses concise and tactical. In a cockpit, brevity saves lives.
- Use standard aviation phraseology wherever appropriate.
- Pause after key information to allow the pilot to acknowledge.
- For routine comms, acknowledgments, and simple questions: 1-3 sentences MAX.
- For procedures and checklists: present items in groups of 3-5, then wait.
- For briefings and flight plans: be thorough but structured — use bullet points, not prose.
- NEVER ramble. If you catch yourself writing a paragraph, stop and restructure.
- After asking a question, STOP. Do not answer your own question.
- After giving a key callout, STOP. Let the Captain respond.
- End radio-style exchanges with "over" or a clear pause point.
"""

# ---------------------------------------------------------------------------
# Flight-phase-specific response style directives.
# ---------------------------------------------------------------------------

_PHASE_STYLE: dict[FlightPhase, str] = {
    FlightPhase.PREFLIGHT: (
        "Phase: PREFLIGHT. Relaxed tone, moderate length. Good time for banter and briefings."
    ),
    FlightPhase.TAXI: (
        "Phase: TAXI. Professional, concise. 1-2 sentences unless reading a checklist."
    ),
    FlightPhase.TAKEOFF: (
        "Phase: TAKEOFF. ULTRA-BRIEF. Callouts only. No humor. No filler."
    ),
    FlightPhase.CLIMB: (
        "Phase: CLIMB. Professional, moderate length. Light humor once established."
    ),
    FlightPhase.CRUISE: (
        "Phase: CRUISE. Conversational, can be more detailed. Good time to teach."
    ),
    FlightPhase.DESCENT: (
        "Phase: DESCENT. Briefing mode. Structured and clear. Minimal humor."
    ),
    FlightPhase.APPROACH: (
        "Phase: APPROACH. ULTRA-BRIEF. Concise callouts only. No humor. No filler."
    ),
    FlightPhase.LANDING: (
        "Phase: LANDING. ULTRA-BRIEF. Callouts only. Crisp and precise."
    ),
    FlightPhase.LANDED: (
        "Phase: LANDED. Relaxed debrief mode. Can use humor. Moderate length."
    ),
}

# ---------------------------------------------------------------------------
# Stop sequences for natural conversation breaks.
# ---------------------------------------------------------------------------

STOP_SEQUENCES: list[str] = ["\nover.", "\nOver.", "\nover", "\nOver"]


def _load_merlin_persona() -> str:
    """Return the full MERLIN system prompt, preferring the on-disk markdown file."""
    if _MERLIN_SYSTEM_PATH.exists():
        try:
            return _MERLIN_SYSTEM_PATH.read_text(encoding="utf-8")
        except Exception:
            logger.warning(
                "Failed to read %s; falling back to inline persona",
                _MERLIN_SYSTEM_PATH,
            )
    return _INLINE_PERSONA


MERLIN_PERSONA: str = _load_merlin_persona()

# ---------------------------------------------------------------------------
# Query classification for response budgeting.
# ---------------------------------------------------------------------------

# Patterns that indicate the pilot wants a detailed response.
_BRIEFING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(brief|briefing|checklist|flight\s*plan|plan\s+a\s+flight)\b", re.I),
    re.compile(r"\b(walk\s+me\s+through|explain|teach|how\s+does)\b", re.I),
    re.compile(r"\b(create|build|make)\s+(a\s+)?(flight\s*plan|route)\b", re.I),
]

# Patterns that indicate a short acknowledgment is expected.
_SHORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(roger|copy|wilco|affirm|negative|check|say again)\b", re.I),
    re.compile(r"^(thanks|thank you|got it|ok|okay)\b", re.I),
    re.compile(r"\b(what\s*'?s?\s+(my|our|the)\s+(altitude|heading|speed|fuel))\b", re.I),
    re.compile(r"\b(how\s+(much|far|long|high|fast))\b", re.I),
    re.compile(r"^(yes|no|yep|nope|yeah)\b", re.I),
]


def classify_query(user_message: str) -> str:
    """Classify a pilot message as 'short', 'briefing', or 'normal'.

    Returns one of: 'short', 'briefing', 'normal'.
    """
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
        "name": "get_sim_state",
        "description": (
            "Retrieve the current simulator state including position, attitude, speeds, "
            "engine parameters, autopilot, radios, fuel, weather, and surface states."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "lookup_airport",
        "description": (
            "Look up airport information by ICAO or FAA identifier. Returns name, location, "
            "elevation, and basic facility data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": (
                        "Airport ICAO or FAA identifier (e.g., KJFK, KLAX, ORL)"
                    ),
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "search_manual",
        "description": (
            "Search the aircraft operating manual and aviation knowledge base. Use this to "
            "look up procedures, limitations, V-speeds, systems descriptions, or any "
            "aircraft-specific information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query describing what to look up",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_checklist",
        "description": (
            "Get the appropriate checklist for a given flight phase. Returns phase-specific "
            "checklist items, preferring aircraft-specific checklists when available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "string",
                    "description": (
                        "Flight phase (PREFLIGHT, TAXI, TAKEOFF, CLIMB, CRUISE, "
                        "DESCENT, APPROACH, LANDING, LANDED)"
                    ),
                },
            },
            "required": ["phase"],
        },
    },
    {
        "name": "create_flight_plan",
        "description": (
            "Create a basic flight plan between two airports. Returns a draft route "
            "structure with departure, destination, and waypoints."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "departure": {
                    "type": "string",
                    "description": "Departure airport identifier",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination airport identifier",
                },
                "altitude": {
                    "type": "integer",
                    "description": "Planned cruise altitude in feet MSL",
                    "default": 5000,
                },
                "route": {
                    "type": "string",
                    "description": "Optional route waypoints separated by spaces",
                    "default": "",
                },
            },
            "required": ["departure", "destination"],
        },
    },
]


class ClaudeClient:
    """Manages conversations with Claude using the MERLIN persona."""

    def __init__(
        self,
        api_key: str,
        model: str,
        sim_client: SimConnectClient,
        context_store: ContextStore,
        max_tokens: int = 1024,
        max_tokens_briefing: int = 2048,
        max_history: int = 20,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._sim_client = sim_client
        self._context_store = context_store
        self._conversation: list[dict[str, Any]] = []
        self._max_history = max_history
        self._max_tokens = max_tokens
        self._max_tokens_briefing = max_tokens_briefing

    def _build_system_prompt(
        self,
        sim_state: SimState,
        context_docs: list[dict[str, Any]],
    ) -> str:
        parts = [MERLIN_PERSONA]

        # Flight-phase-aware response style
        phase = sim_state.flight_phase
        if phase in _PHASE_STYLE:
            parts.append(f"\n--- CURRENT RESPONSE STYLE ---\n{_PHASE_STYLE[phase]}")

        parts.append(f"\n--- CURRENT FLIGHT STATE ---\n{sim_state.telemetry_summary()}")
        parts.append(f"Aircraft: {sim_state.aircraft or 'Unknown'}")
        parts.append(f"On ground: {sim_state.on_ground}")

        if sim_state.autopilot.master:
            ap = sim_state.autopilot
            parts.append(
                f"Autopilot: HDG {ap.heading:.0f} | ALT {ap.altitude:.0f} | "
                f"VS {ap.vertical_speed:+.0f} | IAS {ap.airspeed:.0f}"
            )

        env = sim_state.environment
        parts.append(
            f"Weather: Wind {env.wind_direction:.0f}\u00b0/{env.wind_speed_kts:.0f}kt | "
            f"Vis {env.visibility_sm:.0f}sm | Temp {env.temperature_c:.0f}\u00b0C | "
            f"QNH {env.barometer_inhg:.2f}\"Hg"
        )

        if context_docs:
            parts.append("\n--- RELEVANT REFERENCE MATERIAL ---")
            for doc in context_docs[:3]:
                source = doc.get("metadata", {}).get("source", "unknown")
                parts.append(f"[{source}]\n{doc['content'][:500]}")

        # Append response pacing rules last so they take priority
        parts.append(_RESPONSE_PACING)

        return "\n".join(parts)

    async def chat(
        self,
        user_message: str,
        sim_state: SimState | None = None,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        """Send a message and yield streamed response text chunks.

        Handles tool use loops internally, yielding text as it arrives.
        """
        if sim_state is None:
            try:
                sim_state = await self._sim_client.get_state()
            except Exception:
                sim_state = SimState()

        context_docs = await self._context_store.get_relevant_context(sim_state)
        system = self._build_system_prompt(sim_state, context_docs)

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
                result = await self._execute_tool(tb["name"], tb["input"], sim_state)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb["id"],
                    "content": json.dumps(result),
                })
            self._conversation.append({"role": "user", "content": tool_results})

    async def _execute_tool(
        self, name: str, args: dict[str, Any], sim_state: SimState
    ) -> Any:
        logger.info("Executing tool: %s(%s)", name, args)
        try:
            if name == "get_sim_state":
                return await get_sim_state(self._sim_client)
            elif name == "lookup_airport":
                return await lookup_airport(args["identifier"])
            elif name == "search_manual":
                return await search_manual(
                    args["query"],
                    self._context_store,
                    aircraft_type=sim_state.aircraft,
                )
            elif name == "get_checklist":
                return await get_checklist(
                    args["phase"],
                    self._context_store,
                    aircraft_type=sim_state.aircraft,
                )
            elif name == "create_flight_plan":
                return await create_flight_plan(
                    args["departure"],
                    args["destination"],
                    altitude=args.get("altitude", 5000),
                    route=args.get("route", ""),
                )
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
