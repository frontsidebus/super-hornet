"""Voyager-inspired skill library for Super Hornet.

Stores learned action sequences (skills) in ChromaDB for semantic retrieval.
Skills are composed of ordered steps that map to low-level input actions
(key presses, mouse movements, waits). The library tracks execution success
rates and supports fuzzy search by natural-language description.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SkillStep(BaseModel):
    """A single atomic action within a skill."""

    action: str = Field(
        ...,
        description=(
            'Action type: "press_key", "hold_key", "release_key", '
            '"mouse_click", "mouse_move", "wait"'
        ),
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description='Action parameters, e.g. {"key": "B", "duration": 0.1}',
    )
    description: str = Field(
        default="",
        description='Human-readable note, e.g. "Spool quantum drive"',
    )
    wait_after_ms: int = Field(
        default=0,
        description="Milliseconds to wait before the next step",
    )


class Skill(BaseModel):
    """A reusable, named sequence of input actions."""

    name: str
    description: str
    steps: list[SkillStep]
    preconditions: list[str] = Field(
        default_factory=list,
        description='Required state, e.g. ["player.in_ship == True"]',
    )
    postconditions: list[str] = Field(
        default_factory=list,
        description="Expected state after execution",
    )
    verified: bool = False
    success_count: int = 0
    failure_count: int = 0
    tags: list[str] = Field(
        default_factory=list,
        description='Search tags, e.g. ["navigation", "quantum"]',
    )
    created_at: str = ""
    last_used: str = ""

    @property
    def reliability(self) -> float:
        """Fraction of executions that succeeded (0.0 if never run)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total


class SkillLibrary:
    """Semantic skill store backed by ChromaDB.

    If ChromaDB is unreachable the library degrades gracefully: all queries
    return empty results and ``available`` is ``False``.
    """

    def __init__(
        self,
        chromadb_url: str,
        collection_name: str = "hornet_skills",
    ) -> None:
        self._collection_name = collection_name
        self._collection: Any | None = None
        self._client: Any | None = None

        try:
            import chromadb  # type: ignore[import-untyped]

            self._client = chromadb.HttpClient(chromadb_url)
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
            )
            logger.info(
                "SkillLibrary connected to ChromaDB at %s "
                "(collection=%s, count=%d)",
                chromadb_url,
                collection_name,
                self._collection.count(),
            )
        except Exception:
            logger.warning(
                "ChromaDB unavailable at %s — skill library disabled",
                chromadb_url,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether the ChromaDB connection succeeded."""
        return self._collection is not None

    @property
    def skill_count(self) -> int:
        """Number of skills stored in the library."""
        if not self.available:
            return 0
        try:
            return self._collection.count()  # type: ignore[union-attr]
        except Exception:
            logger.warning("Failed to query skill count", exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def store_skill(self, skill: Skill) -> None:
        """Upsert a skill into the library."""
        if not self.available:
            logger.warning("Skill library unavailable — cannot store skill")
            return

        if not skill.created_at:
            skill.created_at = _now_iso()

        try:
            self._collection.upsert(  # type: ignore[union-attr]
                ids=[skill.name],
                documents=[skill.description],
                metadatas=[{"skill_json": skill.model_dump_json()}],
            )
            logger.info("Stored skill %r", skill.name)
        except Exception:
            logger.error(
                "Failed to store skill %r", skill.name, exc_info=True
            )

    async def find_skill(
        self, query: str, n_results: int = 3
    ) -> list[Skill]:
        """Semantic search for skills matching a natural-language query."""
        if not self.available:
            return []

        try:
            results = self._collection.query(  # type: ignore[union-attr]
                query_texts=[query],
                n_results=n_results,
            )
            return _parse_results(results)
        except Exception:
            logger.error(
                "Skill search failed for %r", query, exc_info=True
            )
            return []

    async def get_skill_by_name(self, name: str) -> Skill | None:
        """Exact lookup by skill name (used as the ChromaDB document ID)."""
        if not self.available:
            return None

        try:
            result = self._collection.get(ids=[name])  # type: ignore[union-attr]
            skills = _parse_results(result)
            return skills[0] if skills else None
        except Exception:
            logger.error(
                "Failed to get skill %r", name, exc_info=True
            )
            return None

    async def mark_success(self, skill_name: str) -> None:
        """Record a successful execution of *skill_name*."""
        skill = await self.get_skill_by_name(skill_name)
        if skill is None:
            logger.warning("Cannot mark success — skill %r not found", skill_name)
            return
        skill.success_count += 1
        skill.verified = True
        skill.last_used = _now_iso()
        await self.store_skill(skill)

    async def mark_failure(self, skill_name: str) -> None:
        """Record a failed execution of *skill_name*."""
        skill = await self.get_skill_by_name(skill_name)
        if skill is None:
            logger.warning("Cannot mark failure — skill %r not found", skill_name)
            return
        skill.failure_count += 1
        skill.last_used = _now_iso()
        await self.store_skill(skill)

    async def list_verified_skills(self) -> list[Skill]:
        """Return every skill that has been successfully executed at least once."""
        if not self.available:
            return []

        try:
            results = self._collection.get(  # type: ignore[union-attr]
                where={"skill_json": {"$ne": ""}},
            )
            skills = _parse_results(results)
            return [s for s in skills if s.verified]
        except Exception:
            logger.error("Failed to list verified skills", exc_info=True)
            return []


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_results(results: dict[str, Any]) -> list[Skill]:
    """Extract ``Skill`` objects from a ChromaDB query/get response."""
    skills: list[Skill] = []
    metadatas = results.get("metadatas") or []
    for meta in metadatas:
        if isinstance(meta, list):
            for entry in meta:
                skill = _meta_to_skill(entry)
                if skill is not None:
                    skills.append(skill)
        elif isinstance(meta, dict):
            skill = _meta_to_skill(meta)
            if skill is not None:
                skills.append(skill)
    return skills


def _meta_to_skill(entry: dict[str, Any]) -> Skill | None:
    raw = entry.get("skill_json")
    if not raw:
        return None
    try:
        return Skill.model_validate_json(raw)
    except Exception:
        logger.warning("Corrupt skill metadata: %s", raw, exc_info=True)
        return None
