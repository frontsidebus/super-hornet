"""ChromaDB-based RAG store for aircraft manuals and aviation knowledge."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

import chromadb

from .sim_client import FlightPhase, SimState

logger = logging.getLogger(__name__)

# Default TTL for cached query results (seconds).  Within the same flight
# phase the relevant documents rarely change, so a generous TTL avoids
# repeated round-trips to ChromaDB.
_CACHE_TTL: float = 60.0

# Map flight phases to relevant document topics for smarter retrieval
PHASE_TOPICS: dict[FlightPhase, list[str]] = {
    FlightPhase.PREFLIGHT: ["preflight", "checklist", "weight and balance", "fuel planning"],
    FlightPhase.TAXI: ["taxi", "ground operations", "airport diagram"],
    FlightPhase.TAKEOFF: ["takeoff", "departure", "engine failure", "V-speeds", "rejected takeoff"],
    FlightPhase.CLIMB: ["climb", "cruise climb", "engine management", "oxygen"],
    FlightPhase.CRUISE: ["cruise", "fuel management", "navigation", "weather"],
    FlightPhase.DESCENT: ["descent", "approach briefing", "STAR", "altimeter"],
    FlightPhase.APPROACH: ["approach", "ILS", "VOR", "RNAV", "minimums", "go-around"],
    FlightPhase.LANDING: ["landing", "crosswind", "short field", "go-around", "flare"],
    FlightPhase.LANDED: ["after landing", "shutdown", "parking"],
}


class _QueryCache:
    """Simple TTL cache keyed by (query_text, n_results, filters_hash).

    Avoids repeated ChromaDB round-trips for identical queries within the
    same flight phase.  The cache is invalidated when the flight phase
    changes.
    """

    def __init__(self, ttl: float = _CACHE_TTL) -> None:
        self._ttl = ttl
        self._entries: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._phase: FlightPhase | None = None

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
        phase: FlightPhase | None = None,
    ) -> list[dict[str, Any]] | None:
        """Return cached results or None on miss / stale / phase change."""
        if phase is not None and phase != self._phase:
            self.invalidate()
            self._phase = phase
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
    """Vector store for aviation documents with flight-phase-aware retrieval.

    Connects to a ChromaDB instance running as a Docker container via the
    HTTP client.  If the server is unavailable at construction time the store
    degrades gracefully: all queries return empty results and document counts
    report zero.

    Includes an in-memory query cache that avoids repeated ChromaDB
    round-trips for the same query within a flight phase.
    """

    def __init__(self, chromadb_url: str = "http://localhost:8000") -> None:
        self._available = False
        self._collection: Any = None
        self._cache = _QueryCache()
        try:
            self._client = chromadb.HttpClient(
                host=self._parse_host(chromadb_url),
                port=self._parse_port(chromadb_url),
            )
            # Verify connectivity with a heartbeat
            self._client.heartbeat()
            self._collection = self._client.get_or_create_collection(
                name="merlin_docs",
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info(
                "Connected to ChromaDB at %s (collection: merlin_docs)", chromadb_url
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

    @property
    def document_count(self) -> int:
        if not self._available or self._collection is None:
            return 0
        try:
            return self._collection.count()
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

        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("Ingested %d chunks from %s", len(chunks), path.name)
        return len(chunks)

    async def query(
        self,
        text: str,
        n_results: int = 5,
        filters: dict[str, Any] | None = None,
        phase: FlightPhase | None = None,
    ) -> list[dict[str, Any]]:
        """Query the store and return matching documents with metadata.

        Results are cached per (text, n_results, filters) tuple and
        automatically invalidated when the flight phase changes.
        """
        if not self._available or self._collection is None:
            return []

        # Check the cache first
        cached = self._cache.get(text, n_results, filters, phase)
        if cached is not None:
            logger.debug("Context store cache hit for query: %s", text[:60])
            return cached

        try:
            where = filters if filters else None
            results = self._collection.query(
                query_texts=[text],
                n_results=n_results,
                where=where,
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

            # Store in cache
            self._cache.put(text, n_results, filters, docs)
            return docs
        except Exception as exc:
            logger.warning("ChromaDB query failed: %s", exc)
            return []

    async def get_relevant_context(
        self,
        sim_state: SimState,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve documents relevant to the current aircraft and flight phase.

        Uses the flight phase for cache invalidation -- when the phase
        changes, previous results are discarded automatically.
        """
        if not self._available:
            return []

        phase = sim_state.flight_phase
        topics = PHASE_TOPICS.get(phase, ["general"])
        query_text = f"{sim_state.aircraft} {' '.join(topics)}"

        if sim_state.aircraft:
            aircraft_results = await self.query(
                query_text,
                n_results=n_results,
                filters={"aircraft_type": sim_state.aircraft},
                phase=phase,
            )
            if aircraft_results:
                return aircraft_results

        return await self.query(
            query_text, n_results=n_results, phase=phase
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
