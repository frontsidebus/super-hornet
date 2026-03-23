"""Regex patterns for Star Citizen game.log parsing.

These patterns are calibrated for Star Citizen Alpha 4.x log format.
The game.log uses a structured format with ISO-ish timestamps and
bracket-delimited or key=value fields.

NOTE: These are initial patterns based on commonly observed SC log structures.
They will need calibration against real game.log output, as CIG frequently
changes log formatting between patches.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

VERSION = "sc_alpha_4x"


class LogEventType(StrEnum):
    PLAYER_KILL = "player_kill"
    PLAYER_DEATH = "player_death"
    VEHICLE_DESTROYED = "vehicle_destroyed"
    LOCATION_CHANGE = "location_change"
    QUANTUM_TRAVEL_START = "quantum_travel_start"
    QUANTUM_TRAVEL_END = "quantum_travel_end"
    CRIME_STAT_CHANGE = "crime_stat_change"
    MISSION_UPDATE = "mission_update"
    ERROR = "error"
    DISCONNECT = "disconnect"
    UNKNOWN = "unknown"


# Timestamp pattern found at the start of most SC game.log lines.
# Format varies but commonly: <YYYY-MM-DDTHH:MM:SS.mmmZ> or similar ISO-style prefix.
_TIMESTAMP_RE = re.compile(
    r"^<?(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)>?\s+"
)

# Common alternate timestamp: angle-bracket delimited epoch or date string
_TIMESTAMP_ALT_RE = re.compile(
    r"^<(?P<timestamp>\d{4}\s\w{3}\s\d{2}\s\d{2}:\d{2}:\d{2})>"
)

# ---------------------------------------------------------------------------
# Compiled regex patterns organized by event type.
# Each pattern uses named groups so parsed data is self-describing.
#
# Star Citizen logs typically look like:
#   <2025-01-15T03:22:10.123Z> [EntityKill] ...
#   <2025-01-15T03:22:10.123Z> [ZoneSystem] Loading zone ...
#   <2025-01-15T03:22:10.123Z> [QuantumDrive] Jump initiated ...
#
# CIG changes these between patches, so patterns should be treated as
# best-effort and updated when log format changes are detected.
# ---------------------------------------------------------------------------

LOG_PATTERNS: dict[LogEventType, list[re.Pattern]] = {  # type: ignore[type-arg]
    LogEventType.PLAYER_KILL: [
        # Pattern: [Kill] or [EntityKill] attacker killed victim with weapon in vehicle
        re.compile(
            r"\[(?:Entity)?Kill\]\s+"
            r"(?P<attacker>[^\s]+)\s+killed\s+(?P<victim>[^\s]+)"
            r"(?:\s+with\s+(?P<weapon>[^\s]+))?"
            r"(?:\s+in\s+(?P<vehicle>[^\s]+))?",
            re.IGNORECASE,
        ),
        # Pattern: key=value style kill event
        re.compile(
            r"\[Combat\]\s+"
            r"Attacker=(?P<attacker>[^\s,;]+)[,;\s]+"
            r"Victim=(?P<victim>[^\s,;]+)"
            r"(?:[,;\s]+Weapon=(?P<weapon>[^\s,;]+))?"
            r"(?:[,;\s]+Vehicle=(?P<vehicle>[^\s,;]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.PLAYER_DEATH: [
        # Pattern: player death notification
        re.compile(
            r"\[(?:Player)?Death\]\s+"
            r"(?P<player>[^\s]+)\s+(?:died|was\s+killed)"
            r"(?:\s+by\s+(?P<cause>[^\s]+))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[Actor\]\s+(?P<player>[^\s]+)\s+(?:destroyed|incapacitated)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.VEHICLE_DESTROYED: [
        re.compile(
            r"\[Vehicle(?:Destruction)?\]\s+"
            r"(?P<vehicle>[^\s]+)\s+(?:destroyed|exploded)"
            r"(?:\s+owner=(?P<owner>[^\s,;]+))?"
            r"(?:\s+by\s+(?P<attacker>[^\s]+))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[EntityDestroy\]\s+"
            r"(?:Vehicle\s+)?(?P<vehicle>[^\s]+)\s+destroyed"
            r"(?:\s+(?:attacker|by)=(?P<attacker>[^\s,;]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.LOCATION_CHANGE: [
        # Pattern: zone loading / location change
        re.compile(
            r"\[Zone(?:System)?\]\s+"
            r"(?:Loading|Entering|Transition(?:ing)?\s+to)\s+"
            r"(?:zone\s+)?(?P<zone>[^\s,;]+)"
            r"(?:\s+(?:on|at|body)[\s=]+(?P<body>[^\s,;]+))?"
            r"(?:\s+(?:system|in)[\s=]+(?P<system>[^\s,;]+))?",
            re.IGNORECASE,
        ),
        # Pattern: ObjectContainer / streaming location
        re.compile(
            r"\[ObjectContainer\]\s+"
            r"(?:Loaded|Streaming)\s+(?P<zone>[^\s,;]+)"
            r"(?:\s+parent=(?P<body>[^\s,;]+))?",
            re.IGNORECASE,
        ),
        # Pattern: simple system/body/zone triplet
        re.compile(
            r"\[Location\]\s+"
            r"System=(?P<system>[^\s,;]+)[,;\s]+"
            r"Body=(?P<body>[^\s,;]+)[,;\s]+"
            r"Zone=(?P<zone>[^\s,;]+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.QUANTUM_TRAVEL_START: [
        re.compile(
            r"\[Quantum(?:Drive)?\]\s+"
            r"(?:Jump\s+)?(?:initiated|started|spooling|calibrating)"
            r"(?:\s+(?:to|destination)[\s=]+(?P<destination>[^\s,;]+))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[QT\]\s+(?:Begin|Start)"
            r"(?:\s+dest=(?P<destination>[^\s,;]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.QUANTUM_TRAVEL_END: [
        re.compile(
            r"\[Quantum(?:Drive)?\]\s+"
            r"(?:Jump\s+)?(?:completed?|arrived|exited|dropped\s+out)"
            r"(?:\s+(?:at|destination)[\s=]+(?P<destination>[^\s,;]+))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[QT\]\s+(?:End|Complete|Arrived)"
            r"(?:\s+dest=(?P<destination>[^\s,;]+))?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.CRIME_STAT_CHANGE: [
        re.compile(
            r"\[Crime(?:Stat)?\]\s+"
            r"(?P<player>[^\s]+)\s+"
            r"(?:crime\s+stat(?:us)?\s+)?(?:changed|updated|set)\s+"
            r"(?:to\s+)?(?P<new_level>\d+)"
            r"(?:\s+from\s+(?P<old_level>\d+))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[Law\]\s+"
            r"CrimeStat\s+(?P<player>[^\s,;]+)\s+"
            r"level=(?P<new_level>\d+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.MISSION_UPDATE: [
        re.compile(
            r"\[Mission(?:s|Manager)?\]\s+"
            r"(?:Mission\s+)?(?P<mission_id>[^\s,;]+)\s+"
            r"(?:status\s+)?(?:changed\s+to|updated|=)\s*(?P<status>[^\s,;]+)"
            r"(?:\s+title=(?P<title>[^\n]+))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[Mission\]\s+"
            r"(?P<status>Accepted|Completed|Failed|Abandoned|Updated)"
            r"\s+(?P<mission_id>[^\s,;]+)"
            r"(?:\s+\"(?P<title>[^\"]+)\")?",
            re.IGNORECASE,
        ),
    ],
    LogEventType.ERROR: [
        re.compile(
            r"\[(?:Error|FATAL|CRITICAL|Exception)\]\s+(?P<message>.+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|\s)(?:ERROR|FATAL|EXCEPTION):\s+(?P<message>.+)",
            re.IGNORECASE,
        ),
    ],
    LogEventType.DISCONNECT: [
        re.compile(
            r"\[(?:Network|Connection|Disconnect)\]\s+"
            r"(?P<reason>(?:disconnected|lost\s+connection|timeout|kicked).+)",
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

    Tries the primary ISO-style format first, then falls back to
    the alternate format. Returns None if no timestamp is found.
    """
    match = _TIMESTAMP_RE.match(line)
    if not match:
        match = _TIMESTAMP_ALT_RE.match(line)
    if not match:
        return None

    raw_ts = match.group("timestamp")

    # Try ISO format first (most common in SC 4.x)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y %b %d %H:%M:%S",
    ):
        try:
            return datetime.strptime(raw_ts, fmt)
        except ValueError:
            continue

    return None


def match_line(line: str) -> tuple[LogEventType, dict[str, Any]] | None:
    """Try all compiled patterns against a log line.

    Returns the first matching (event_type, captured_data) tuple,
    or None if no pattern matches. Patterns are tried in definition
    order, so more specific event types are checked before generic ones.
    """
    for event_type, patterns in LOG_PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(line)
            if m:
                # Filter out None-valued groups for cleaner data
                data = {k: v for k, v in m.groupdict().items() if v is not None}
                return event_type, data
    return None
