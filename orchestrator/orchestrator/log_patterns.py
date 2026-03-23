"""Regex patterns for Star Citizen game.log parsing.

Calibrated against Star Citizen Alpha 4.x log format (March 2026).
The game.log uses this format:
  <2026-03-23T14:15:20.030Z> [Level] <EventTag> message [Team][Category]

Where:
  - Timestamp is ISO 8601 in angle brackets
  - Level is Notice, Error, Warning, etc.
  - EventTag is the specific event identifier in angle brackets
  - Message body follows
  - Team and Category tags in square brackets at the end
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

VERSION = "sc_alpha_4x_calibrated"


class LogEventType(StrEnum):
    PLAYER_KILL = "player_kill"
    PLAYER_DEATH = "player_death"
    VEHICLE_DESTROYED = "vehicle_destroyed"
    LOCATION_CHANGE = "location_change"
    QUANTUM_TRAVEL_START = "quantum_travel_start"
    QUANTUM_TRAVEL_END = "quantum_travel_end"
    CRIME_STAT_CHANGE = "crime_stat_change"
    MISSION_UPDATE = "mission_update"
    NOTIFICATION = "notification"
    ERROR = "error"
    DISCONNECT = "disconnect"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Timestamp extraction
# ---------------------------------------------------------------------------

# SC 4.x format: <2026-03-23T14:15:20.030Z>
_TIMESTAMP_RE = re.compile(
    r"^<(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)>"
)


# ---------------------------------------------------------------------------
# Log level extraction: [Notice], [Error], [Warning]
# ---------------------------------------------------------------------------

_LEVEL_RE = re.compile(r"\[(?P<level>Notice|Error|Warning|Fatal)\]")


# ---------------------------------------------------------------------------
# Location inference from object container paths
# ---------------------------------------------------------------------------

# Maps path fragments to (system, body, zone) tuples
_LOCATION_HINTS: dict[str, tuple[str, str, str]] = {
    "newbab": ("Stanton", "microTech", "New Babbage"),
    "lorville": ("Stanton", "Hurston", "Lorville"),
    "area18": ("Stanton", "ArcCorp", "Area18"),
    "orison": ("Stanton", "Crusader", "Orison"),
    "grimhex": ("Stanton", "Yela", "GrimHEX"),
    "tressler": ("Stanton", "microTech", "Port Tressler"),
    "everus": ("Stanton", "Hurston", "Everus Harbor"),
    "baijini": ("Stanton", "ArcCorp", "Baijini Point"),
    "portolisar": ("Stanton", "Crusader", "Port Olisar"),
    "pyro_gate": ("Pyro", "", "Pyro Gateway"),
    "ruin_station": ("Pyro", "", "Ruin Station"),
}

# Map OOC container names to (system, body)
_OOC_BODIES: dict[str, tuple[str, str]] = {
    "OOC_Stanton_1_Hurston": ("Stanton", "Hurston"),
    "OOC_Stanton_1a_Ariel": ("Stanton", "Ariel"),
    "OOC_Stanton_1b_Aberdeen": ("Stanton", "Aberdeen"),
    "OOC_Stanton_1c_Magda": ("Stanton", "Magda"),
    "OOC_Stanton_1d_Ita": ("Stanton", "Ita"),
    "OOC_Stanton_2_Crusader": ("Stanton", "Crusader"),
    "OOC_Stanton_2a_Cellin": ("Stanton", "Cellin"),
    "OOC_Stanton_2b_Daymar": ("Stanton", "Daymar"),
    "OOC_Stanton_2c_Yela": ("Stanton", "Yela"),
    "OOC_Stanton_3_ArcCorp": ("Stanton", "ArcCorp"),
    "OOC_Stanton_3a_Lyria": ("Stanton", "Lyria"),
    "OOC_Stanton_3b_Wala": ("Stanton", "Wala"),
    "OOC_Stanton_4_Microtech": ("Stanton", "microTech"),
    "OOC_Stanton_4a_Calliope": ("Stanton", "Calliope"),
    "OOC_Stanton_4b_Clio": ("Stanton", "Clio"),
    "OOC_Stanton_4c_Euterpe": ("Stanton", "Euterpe"),
}

# Ship name patterns from entity references (MANUFACTURER_Model_ID)
_SHIP_RE = re.compile(
    r"(?:ANVL|AEGS|DRAK|RSI|ORIG|MISC|CNOU|CRUS|ARGO|GCAT|TMBL|ESPR|GATC|AOPA)"
    r"_(?P<ship_model>[A-Za-z0-9_]+?)_\d+",
)


# ---------------------------------------------------------------------------
# Event patterns — matched against the full line after timestamp
# ---------------------------------------------------------------------------

LOG_PATTERNS: dict[LogEventType, list[re.Pattern]] = {  # type: ignore[type-arg]
    LogEventType.PLAYER_KILL: [
        # Kill/death tracking from combat log
        re.compile(
            r"<(?:Kill|EntityKill|CombatLog)>\s+"
            r"(?P<attacker>[^\s]+)\s+killed\s+(?P<victim>[^\s]+)"
            r"(?:\s+with\s+(?P<weapon>[^\s]+))?"
            r"(?:\s+in\s+(?P<vehicle>[^\s]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.PLAYER_DEATH: [
        re.compile(
            r"<(?:PlayerDeath|Death|Actor)>\s+"
            r"(?P<player>[^\s]+)\s+(?:died|was\s+killed|incapacitated)"
            r"(?:\s+(?:by|cause)[\s:=]+(?P<cause>[^\[]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.VEHICLE_DESTROYED: [
        re.compile(
            r"<(?:VehicleDestruction|EntityDestroy)>\s+"
            r"(?P<vehicle>[^\s]+)\s+(?:destroyed|exploded)"
            r"(?:\s+(?:by|attacker)[\s:=]+(?P<attacker>[^\[]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.LOCATION_CHANGE: [
        # Object container loading with location hints (e.g., hangar_lrgtop_001_newbab)
        re.compile(
            r"<StatObjLoad[^>]*>\s+'data/objectcontainers/pu/loc/"
            r"(?P<path>[^']+)'",
            re.IGNORECASE,
        ),
        # OOC planet/moon cell data
        re.compile(
            r"name:\s+(?P<ooc_name>OOC_[A-Za-z0-9_]+)",
        ),
        # Loading screen / zone transitions
        re.compile(
            r"<(?:Zone|ZoneSystem|LoadingScreen)>\s+"
            r"(?:Loading|Entering|Transition)\s+(?:to\s+)?(?P<zone>[^\[]+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.QUANTUM_TRAVEL_START: [
        # QT route data and navigation
        re.compile(
            r"<(?:QuantumDrive|QT|ItemNavigation)>.*"
            r"(?:Jump\s+initiated|Spooling|GetStarmapRouteSegmentData)"
            r"(?:.*destination[\s=]+(?P<destination>[^\[\s,]+))?",
            re.IGNORECASE,
        ),
        # Ship navigation reference (contains ship name)
        re.compile(
            r"\[ItemNavigation\].*\|\s+(?:NOT\s+AUTH|AUTH)\s+\|\s+"
            r"(?P<ship>[A-Z]{4}_[A-Za-z0-9_]+)\[",
        ),
    ],
    LogEventType.QUANTUM_TRAVEL_END: [
        re.compile(
            r"<(?:QuantumDrive|QT)>.*"
            r"(?:Jump\s+complete|Arrived|Exited|Dropped)"
            r"(?:.*(?:at|destination)[\s=]+(?P<destination>[^\[\s,]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.CRIME_STAT_CHANGE: [
        re.compile(
            r"<(?:CrimeStat|Law|Infraction)>\s+"
            r"(?P<player>[^\s]+)\s+"
            r"(?:crime\s*stat(?:us)?|level)\s*(?:changed|updated|set)\s*(?:to\s*)?"
            r"(?P<new_level>\d+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.MISSION_UPDATE: [
        # Mission notifications
        re.compile(
            r"<(?:Mission|MissionManager)>\s+"
            r"(?P<status>Accepted|Completed|Failed|Abandoned|Updated)"
            r"\s+(?P<mission_id>[^\s\[]+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.NOTIFICATION: [
        # HUD notifications
        re.compile(
            r"<SHUDEvent_OnNotification>\s+Added\s+notification\s+"
            r"\"(?P<message>[^\"]+)\"",
        ),
    ],
    LogEventType.ERROR: [
        # Errors with the SC format: [Error] <EventTag> message
        re.compile(
            r"\[Error\]\s+<(?P<tag>[^>]+)>\s+(?P<message>[^\[]+)",
        ),
        re.compile(
            r"\[Fatal\]\s+(?P<message>.+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.DISCONNECT: [
        re.compile(
            r"<(?:Disconnect|NetworkError|ConnectionLost)>\s+"
            r"(?P<reason>[^\[]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:DISCONNECT|CONNECTION_LOST|TIMEOUT)\s*:?\s*(?P<reason>.+)?",
            re.IGNORECASE,
        ),
    ],
}


def parse_timestamp(line: str) -> datetime | None:
    """Extract and parse the timestamp from a game.log line.

    SC 4.x format: <2026-03-23T14:15:20.030Z>
    """
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None

    raw_ts = match.group("timestamp")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(raw_ts, fmt)
        except ValueError:
            continue

    return None


def infer_location(line: str) -> tuple[str, str, str] | None:
    """Try to infer (system, body, zone) from a log line.

    Checks for:
    1. Object container paths with location hints (e.g., newbab → New Babbage)
    2. OOC planet/moon names (e.g., OOC_Stanton_4_Microtech)
    """
    line_lower = line.lower()

    # Check location hints from object container paths
    for hint, location in _LOCATION_HINTS.items():
        if hint in line_lower:
            return location

    # Check OOC body names
    for ooc_name, (system, body) in _OOC_BODIES.items():
        if ooc_name in line:
            return (system, body, "")

    return None


def extract_ship_name(line: str) -> str | None:
    """Extract a ship model name from entity references.

    E.g., 'ANVL_Asgard_9716674112388' → 'Asgard'
    """
    match = _SHIP_RE.search(line)
    if match:
        return match.group("ship_model").replace("_", " ")
    return None


def match_line(line: str) -> tuple[LogEventType, dict[str, Any]] | None:
    """Try all compiled patterns against a log line.

    Returns the first matching (event_type, captured_data) tuple,
    or None if no pattern matches.
    """
    for event_type, patterns in LOG_PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(line)
            if m:
                data = {k: v for k, v in m.groupdict().items() if v is not None}

                # Enrich with location inference for location events
                if event_type == LogEventType.LOCATION_CHANGE:
                    loc = infer_location(line)
                    if loc:
                        data["system"] = loc[0]
                        data["body"] = loc[1]
                        data["zone"] = loc[2]

                # Extract ship name if present
                ship = extract_ship_name(line)
                if ship:
                    data["ship"] = ship

                return event_type, data
    return None
