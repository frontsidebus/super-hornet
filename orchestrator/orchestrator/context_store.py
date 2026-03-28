"""ChromaDB-based RAG store for Star Citizen knowledge base."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from functools import partial
from pathlib import Path
from typing import Any

import chromadb

from .game_state import GameActivity, GameState

logger = logging.getLogger(__name__)

# Default TTL for cached query results (seconds).
_CACHE_TTL: float = 60.0

# Map game activities to relevant document topics for smarter retrieval
ACTIVITY_TOPICS: dict[GameActivity, list[str]] = {
    GameActivity.IDLE: ["general", "ship builds", "loadouts", "game mechanics"],
    GameActivity.ON_FOOT: ["FPS", "ground combat", "locations", "missions"],
    GameActivity.SHIP_IDLE: ["ship startup", "power management", "pre-flight", "components"],
    GameActivity.SHIP_FLIGHT: ["navigation", "flight controls", "SCM", "afterburner"],
    GameActivity.QUANTUM_TRAVEL: ["quantum travel", "jump points", "route planning", "fuel"],
    GameActivity.COMBAT: [
        "combat", "weapons", "shields", "missiles", "countermeasures", "evasion", "power triangle",
    ],
    GameActivity.MINING: [
        "mining", "rock composition", "laser", "extraction", "refining", "quantanium",
    ],
    GameActivity.SALVAGE: ["salvage", "components", "materials", "reclamation"],
    GameActivity.TRADING: ["trading", "commodities", "cargo", "trade routes", "profit margins"],
    GameActivity.LANDING: ["landing", "approach", "landing pads", "hangars", "ATC"],
    GameActivity.EVA: ["EVA", "spacewalk", "zero gravity", "repair"],
    GameActivity.ENGINEERING: [
        "engineering", "power distribution", "overclocking", "component repair", "fire suppression",
    ],
}


class _QueryCache:
    """Simple TTL cache keyed by (query_text, n_results, filters_hash).

    Avoids repeated ChromaDB round-trips for identical queries within the
    same game activity.  The cache is invalidated when the activity changes.
    """

    def __init__(self, ttl: float = _CACHE_TTL) -> None:
        self._ttl = ttl
        self._entries: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._activity: GameActivity | None = None

    def _make_key(
        self,
        text: str,
        n_results: int,
        filters: dict[str, Any] | None,
    ) -> str:
        filter_str = str(sorted(filters.items())) if filters else ""
        raw = f"{text}|{n_results}|{filter_str}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(
        self,
        text: str,
        n_results: int,
        filters: dict[str, Any] | None,
        activity: GameActivity | None = None,
    ) -> list[dict[str, Any]] | None:
        """Return cached results or None on miss / stale / activity change."""
        if activity is not None and activity != self._activity:
            self.invalidate()
            self._activity = activity
            return None
        key = self._make_key(text, n_results, filters)
        entry = self._entries.get(key)
        if entry is None:
            return None
        ts, results = entry
        if time.monotonic() - ts > self._ttl:
            del self._entries[key]
            return None
        return results

    def put(
        self,
        text: str,
        n_results: int,
        filters: dict[str, Any] | None,
        results: list[dict[str, Any]],
    ) -> None:
        key = self._make_key(text, n_results, filters)
        self._entries[key] = (time.monotonic(), results)

    def invalidate(self) -> None:
        """Clear all cached entries."""
        self._entries.clear()


class ContextStore:
    """Vector store for Star Citizen knowledge with activity-aware retrieval.

    Connects to a ChromaDB instance running as a Docker container via the
    HTTP client.  If the server is unavailable at construction time the store
    degrades gracefully: all queries return empty results and document counts
    report zero.

    Includes an in-memory query cache that avoids repeated ChromaDB
    round-trips for the same query within a game activity.
    """

    def __init__(
        self,
        chromadb_url: str = "http://localhost:8000",
        collection_name: str = "hornet_knowledge",
    ) -> None:
        self._available = False
        self._collection: Any = None
        self._cache = _QueryCache()
        self._collection_name = collection_name
        try:
            self._client = chromadb.HttpClient(
                host=self._parse_host(chromadb_url),
                port=self._parse_port(chromadb_url),
            )
            self._client.heartbeat()
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info(
                "Connected to ChromaDB at %s (collection: %s)",
                chromadb_url,
                collection_name,
            )
        except Exception as exc:
            logger.warning(
                "ChromaDB unavailable at %s (%s); context store disabled. "
                "RAG queries will return empty results.",
                chromadb_url,
                exc,
            )

    # --- helpers for parsing the URL -----------------------------------------

    @staticmethod
    def _parse_host(url: str) -> str:
        """Extract host from a URL like http://localhost:8000."""
        url = url.replace("http://", "").replace("https://", "")
        return url.split(":")[0].split("/")[0]

    @staticmethod
    def _parse_port(url: str) -> int:
        """Extract port from a URL like http://localhost:8000."""
        url = url.replace("http://", "").replace("https://", "")
        parts = url.split(":")
        if len(parts) >= 2:
            try:
                return int(parts[1].split("/")[0])
            except ValueError:
                pass
        return 8000

    # --- public API ----------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    async def document_count(self) -> int:
        """Number of documents in the store."""
        if not self._available or self._collection is None:
            return 0
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._collection.count)
        except Exception:
            return 0

    async def ingest_document(
        self,
        path: str | Path,
        metadata: dict[str, Any] | None = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> int:
        """Ingest a text document into the vector store.

        Splits the document into overlapping chunks and stores each with
        metadata for filtered retrieval. Returns the number of chunks ingested.
        """
        if not self._available or self._collection is None:
            logger.warning("Context store unavailable; cannot ingest %s", path)
            return 0

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        base_meta = {"source": str(path), "filename": path.name}
        if metadata:
            base_meta.update(metadata)

        chunks = self._split_text(text, chunk_size, chunk_overlap)
        if not chunks:
            return 0

        ids = []
        documents = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            doc_hash = hashlib.sha256(f"{path}:{i}".encode()).hexdigest()[:16]
            ids.append(f"{path.stem}_{doc_hash}")
            documents.append(chunk)
            metadatas.append({**base_meta, "chunk_index": i})

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._collection.upsert,
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            ),
        )
        logger.info("Ingested %d chunks from %s", len(chunks), path.name)
        return len(chunks)

    async def query(
        self,
        text: str,
        n_results: int = 5,
        filters: dict[str, Any] | None = None,
        activity: GameActivity | None = None,
    ) -> list[dict[str, Any]]:
        """Query the store and return matching documents with metadata.

        Results are cached per (text, n_results, filters) tuple and
        automatically invalidated when the game activity changes.
        """
        if not self._available or self._collection is None:
            return []

        cached = self._cache.get(text, n_results, filters, activity)
        if cached is not None:
            logger.debug("Context store cache hit for query: %s", text[:60])
            return cached

        try:
            where = filters if filters else None
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                partial(
                    self._collection.query,
                    query_texts=[text],
                    n_results=n_results,
                    where=where,
                ),
            )

            docs: list[dict[str, Any]] = []
            if results["documents"] and results["metadatas"]:
                distances = (
                    results["distances"][0]
                    if results.get("distances")
                    else [0.0] * len(results["documents"][0])
                )
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    distances,
                    strict=False,
                ):
                    docs.append(
                        {"content": doc, "metadata": meta, "distance": dist}
                    )

            self._cache.put(text, n_results, filters, docs)
            return docs
        except Exception as exc:
            logger.warning("ChromaDB query failed: %s", exc)
            return []

    async def get_relevant_context(
        self,
        game_state: GameState,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve documents relevant to the current ship and game activity.

        Uses the game activity for cache invalidation -- when the activity
        changes, previous results are discarded automatically.
        """
        if not self._available:
            return []

        activity = game_state.activity
        topics = ACTIVITY_TOPICS.get(activity, ["general"])
        ship_name = game_state.ship.name if game_state.player.in_ship else ""
        query_text = f"{ship_name} {' '.join(topics)}".strip()

        if ship_name:
            ship_results = await self.query(
                query_text,
                n_results=n_results,
                filters={"ship_name": ship_name},
                activity=activity,
            )
            if ship_results:
                return ship_results

        return await self.query(
            query_text, n_results=n_results, activity=activity
        )

    @staticmethod
    def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk.strip())
            start += chunk_size - overlap
        return chunks
