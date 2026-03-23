"""Tool implementations callable by Claude during conversations."""

from __future__ import annotations

import logging
from typing import Any

from .game_state import GameActivity, GameState
from .game_client import GameStateClient
from .context_store import ContextStore
from .uex_client import UEXClient
from .skill_library import Skill, SkillLibrary

logger = logging.getLogger(__name__)

# Activity-appropriate procedures (simplified defaults; real ones come from the context store)
DEFAULT_PROCEDURES: dict[GameActivity, list[str]] = {
    GameActivity.SHIP_IDLE: [
        "Power on - COMPLETE",
        "Shields - CHECK",
        "Weapons - CHECK",
        "Fuel - CHECK",
    ],
    GameActivity.SHIP_FLIGHT: [
        "Systems - MONITOR",
        "Navigation - SET",
        "Comms - CHECK",
    ],
    GameActivity.QUANTUM_TRAVEL: [
        "Destination - SET (F2)",
        "Quantum fuel - CHECK",
        "Route - CLEAR",
        "Quantum drive - SPOOL (B)",
        "Quantum drive - ENGAGE (hold B)",
    ],
    GameActivity.COMBAT: [
        "Weapons - HOT",
        "Shields - BALANCED",
        "Countermeasures - READY",
        "Target - LOCKED",
    ],
    GameActivity.MINING: [
        "Scanner - ACTIVE",
        "Laser - CONFIGURED",
        "Rock - TARGETED",
        "Extraction mode - ENGAGED",
    ],
    GameActivity.SALVAGE: [
        "Salvage mode - ACTIVE",
        "Components - IDENTIFIED",
        "Extraction area - CLEAR",
    ],
    GameActivity.TRADING: [
        "Terminal - ACCESS",
        "Commodity prices - CHECKED",
        "Cargo space - VERIFIED",
        "Route - PLANNED",
    ],
    GameActivity.LANDING: [
        "Approach clearance - OBTAINED",
        "Landing gear - DOWN (N)",
        "Speed - REDUCED",
        "Pad - IDENTIFIED",
    ],
}


async def get_game_state(game_client: GameStateClient) -> dict[str, Any]:
    """Return the current game state snapshot."""
    try:
        state: GameState = await game_client.get_state()
    except ConnectionError:
        return {"error": "Not connected to game client"}
    except Exception as exc:
        logger.warning("Failed to retrieve game state: %s", exc)
        return {"error": f"Failed to retrieve game state: {exc}"}

    return state.to_dict()


async def lookup_commodity(
    commodity: str,
    uex_client: UEXClient,
    location: str = "",
) -> dict[str, Any]:
    """Look up commodity pricing and availability via the UEX API."""
    commodity = commodity.strip()
    if not commodity:
        return {"error": "Commodity name is required"}

    try:
        result = await uex_client.lookup_commodity(commodity, location=location)
        return result
    except Exception as exc:
        logger.warning("Commodity lookup failed for %s: %s", commodity, exc)
        return {"error": f"Commodity lookup failed: {exc}"}


async def search_knowledge(
    query: str,
    context_store: ContextStore,
    ship_name: str = "",
    n_results: int = 5,
) -> list[dict[str, Any]]:
    """Search the knowledge base via vector store (RAG)."""
    try:
        filters = None
        if ship_name:
            filters = {"ship_name": ship_name}

        results = await context_store.query(query, n_results=n_results, filters=filters)
        return [
            {"content": r["content"], "source": r["metadata"].get("source", "unknown")}
            for r in results
        ]
    except Exception as exc:
        logger.warning("Knowledge search failed for query '%s': %s", query, exc)
        return [{"error": f"Knowledge search failed: {exc}"}]


async def get_ship_status(game_client: GameStateClient) -> dict[str, Any]:
    """Return detailed ship status including components and loadout."""
    try:
        status = await game_client.get_ship_status()
        return status
    except ConnectionError:
        return {"error": "Not connected to game client"}
    except Exception as exc:
        logger.warning("Failed to retrieve ship status: %s", exc)
        return {"error": f"Failed to retrieve ship status: {exc}"}


async def plan_trade_route(
    origin: str,
    destination: str,
    cargo_scu: int,
    uex_client: UEXClient,
) -> dict[str, Any]:
    """Plan a trade route between two locations with the given cargo capacity."""
    origin = origin.strip()
    destination = destination.strip()

    if not origin or not destination:
        return {"error": "Both origin and destination are required"}
    if cargo_scu <= 0:
        return {"error": "Cargo SCU must be a positive integer"}

    try:
        route = await uex_client.plan_trade_route(
            origin=origin,
            destination=destination,
            cargo_scu=cargo_scu,
        )
        return route
    except Exception as exc:
        logger.warning(
            "Trade route planning failed (%s -> %s): %s", origin, destination, exc
        )
        return {"error": f"Trade route planning failed: {exc}"}


async def get_procedure(
    activity: str | GameActivity,
    context_store: ContextStore,
    ship_name: str = "",
) -> dict[str, Any]:
    """Return the procedure/checklist appropriate for the given activity.

    Accepts the activity as a string (case-insensitive) or a GameActivity enum.
    """
    if isinstance(activity, str):
        activity_upper = activity.strip().upper()
        try:
            activity = GameActivity(activity_upper)
        except ValueError:
            # Try matching by enum name as well
            matched = None
            for ga in GameActivity:
                if ga.value == activity_upper or ga.name == activity_upper:
                    matched = ga
                    break
            if matched is None:
                valid = ", ".join(ga.value for ga in GameActivity)
                return {"error": f"Unknown activity: {activity}. Valid activities: {valid}"}
            activity = matched

    # Try to find a ship-specific procedure in the knowledge base
    if ship_name:
        try:
            results = await context_store.query(
                f"{ship_name} {activity.value.lower()} procedure",
                n_results=1,
                filters={"ship_name": ship_name},
            )
            if results:
                return {
                    "activity": activity.value,
                    "ship": ship_name,
                    "source": "knowledge_base",
                    "procedure": results[0]["content"],
                }
        except Exception as exc:
            logger.warning(
                "Knowledge base lookup failed for procedure %s: %s",
                activity.value,
                exc,
            )

    # Fall back to generic procedure
    items = DEFAULT_PROCEDURES.get(activity, ["No procedure available for this activity"])
    return {
        "activity": activity.value,
        "ship": ship_name or "generic",
        "source": "default",
        "items": items,
    }


async def get_skill(query: str, skill_library: SkillLibrary) -> dict[str, Any]:
    """Search the skill library for a matching skill."""
    query = query.strip()
    if not query:
        return {"error": "Query string is required"}

    try:
        skill: Skill | None = await skill_library.search(query)
        if skill is None:
            return {"error": f"No skill found matching '{query}'"}
        return skill.to_dict()
    except Exception as exc:
        logger.warning("Skill library search failed for '%s': %s", query, exc)
        return {"error": f"Skill library search failed: {exc}"}
