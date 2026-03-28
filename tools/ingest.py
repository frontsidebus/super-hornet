#!/usr/bin/env python3
"""
Document Ingestion CLI for Super Hornet Context Store.

Ingests ship manuals, game guides, and other reference documents into
ChromaDB for retrieval-augmented generation.

Usage:
    python ingest.py --file path/to/manual.pdf --type poh --aircraft "Super Hornet"
    python ingest.py --file path/to/sc_manual.md --type game_manual
    python ingest.py --dir path/to/docs/ --type reference
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Optional, Sequence

import chromadb
from chromadb.config import Settings as ChromaSettings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("hornet.ingest")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".txt", ".md"}
VALID_DOC_TYPES: list[str] = ["poh", "game_manual", "faa_reference", "checklist"]
CHUNK_TARGET_TOKENS: int = 1000
CHUNK_OVERLAP_TOKENS: int = 200
# Rough ratio: 1 token ~ 4 characters for English text.
CHARS_PER_TOKEN: int = 4
CHUNK_TARGET_CHARS: int = CHUNK_TARGET_TOKENS * CHARS_PER_TOKEN
CHUNK_OVERLAP_CHARS: int = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN

DEFAULT_CHROMA_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "chroma_db",
)
COLLECTION_NAME: str = "hornet_docs"


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------
class EmbeddingProvider:
    """Generates embeddings via Anthropic Voyager or local sentence-transformers."""

    def __init__(self) -> None:
        self._local_model: object | None = None
        self._use_local: bool = False
        self._anthropic_client: object | None = None
        self._init_provider()

    # -- initialisation -----------------------------------------------------

    def _init_provider(self) -> None:
        """Try Anthropic first, fall back to local sentence-transformers."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            try:
                import anthropic  # noqa: F811

                self._anthropic_client = anthropic.Anthropic(api_key=api_key)
                # Verify the key works by making a minimal call later on first use.
                log.info("Anthropic API key found — will use Voyage embeddings via Anthropic.")
                return
            except Exception as exc:
                log.warning("Failed to initialise Anthropic client: %s", exc)

        # Fallback: local model
        log.info("Falling back to local sentence-transformers model for embeddings.")
        self._use_local = True
        try:
            from sentence_transformers import SentenceTransformer

            self._local_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            log.error(
                "Neither Anthropic API key nor sentence-transformers is available. "
                "Install sentence-transformers: pip install sentence-transformers"
            )
            sys.exit(1)

    # -- public API ---------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a list of embedding vectors for *texts*."""
        if self._use_local:
            return self._embed_local(texts)
        return self._embed_anthropic(texts)

    # -- backends -----------------------------------------------------------

    def _embed_anthropic(self, texts: list[str]) -> list[list[float]]:
        """Embed via the Anthropic Voyager endpoint (batched)."""
        try:
            import anthropic as _anthropic  # noqa: F811

            client: _anthropic.Anthropic = self._anthropic_client  # type: ignore[assignment]
            # Anthropic exposes voyage embeddings through their API.
            # Use the voyage-3 model for best quality.
            batch_size = 64
            all_embeddings: list[list[float]] = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                response = client.embeddings.create(
                    model="voyage-3",
                    input=batch,
                )
                all_embeddings.extend([item.embedding for item in response.data])
            return all_embeddings
        except Exception as exc:
            log.warning("Anthropic embedding call failed (%s), falling back to local.", exc)
            self._use_local = True
            self._init_local_model()
            return self._embed_local(texts)

    def _init_local_model(self) -> None:
        if self._local_model is None:
            from sentence_transformers import SentenceTransformer

            self._local_model = SentenceTransformer("all-MiniLM-L6-v2")

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        self._init_local_model()
        vectors = self._local_model.encode(texts, show_progress_bar=False)  # type: ignore[union-attr]
        return [v.tolist() for v in vectors]


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------
def extract_text(file_path: Path) -> str:
    """Extract raw text from a PDF, TXT, or Markdown file."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf(file_path)
    elif suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf(file_path: Path) -> str:
    """Extract text from a PDF using PyMuPDF (fitz) or pdfplumber."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(file_path))
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n\n".join(pages)
    except ImportError:
        pass

    try:
        import pdfplumber

        with pdfplumber.open(str(file_path)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n\n".join(pages)
    except ImportError:
        log.error(
            "No PDF reader available. Install PyMuPDF (pip install pymupdf) "
            "or pdfplumber (pip install pdfplumber)."
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
# Patterns that typically start a new section in aviation documents.
_SECTION_BOUNDARY_RE = re.compile(
    r"""
    (?:^|\n)                       # beginning of text or newline
    (?:
        \#{1,4}\s                  # Markdown headings
        | SECTION\s+\d+           # SECTION 1, SECTION 2, ...
        | CHAPTER\s+\d+           # CHAPTER 1, ...
        | \d+\.\d+\s+[A-Z]       # Numbered sub-sections like 3.2 ENGINE
        | ={3,}                    # === dividers
        | -{3,}                    # --- dividers
        | (?:NORMAL\s+PROCEDURES|EMERGENCY\s+PROCEDURES|LIMITATIONS|PERFORMANCE)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _detect_sections(text: str) -> list[tuple[str, str]]:
    """Split text into (section_title, section_body) pairs.

    If no clear section boundaries are found the whole text is returned as a
    single unnamed section.
    """
    boundaries = list(_SECTION_BOUNDARY_RE.finditer(text))
    if not boundaries:
        return [("", text)]

    sections: list[tuple[str, str]] = []
    # Text before the first boundary is the preamble.
    if boundaries[0].start() > 0:
        sections.append(("preamble", text[: boundaries[0].start()]))

    for idx, match in enumerate(boundaries):
        title_line = match.group().strip().lstrip("#").strip()
        start = match.end()
        end = boundaries[idx + 1].start() if idx + 1 < len(boundaries) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((title_line, body))

    return sections


def _chunk_text(text: str, target: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split *text* into chunks of roughly *target* characters with *overlap*.

    Tries to break on paragraph or sentence boundaries when possible.
    """
    if len(text) <= target:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + target

        if end < len(text):
            # Try to find a paragraph break near the target.
            para_break = text.rfind("\n\n", start + target // 2, end + overlap)
            if para_break != -1:
                end = para_break + 2
            else:
                # Fall back to sentence boundary.
                sent_break = text.rfind(". ", start + target // 2, end + overlap)
                if sent_break != -1:
                    end = sent_break + 2
        else:
            end = len(text)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Move forward, leaving overlap.
        start = max(start + 1, end - overlap)

    return chunks


def chunk_document(
    text: str,
) -> list[dict[str, str]]:
    """Chunk a document respecting section boundaries.

    Returns a list of dicts with keys ``section`` and ``text``.
    """
    sections = _detect_sections(text)
    results: list[dict[str, str]] = []

    for section_title, section_body in sections:
        for chunk in _chunk_text(section_body):
            results.append({"section": section_title, "text": chunk})

    return results


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------
def get_collection(
    chroma_path: str,
) -> chromadb.Collection:
    """Return (or create) the Hornet documents collection."""
    Path(chroma_path).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=chroma_path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def file_content_hash(text: str) -> str:
    """SHA-256 hex digest (first 16 chars) of the document text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------
def ingest_file(
    file_path: Path,
    doc_type: str,
    aircraft_type: Optional[str],
    chroma_path: str,
    embedder: EmbeddingProvider,
) -> int:
    """Ingest a single file into ChromaDB. Returns the number of chunks stored."""
    log.info("Ingesting %s (type=%s, aircraft=%s)", file_path.name, doc_type, aircraft_type or "N/A")

    text = extract_text(file_path)
    if not text.strip():
        log.warning("No text extracted from %s — skipping.", file_path.name)
        return 0

    content_hash = file_content_hash(text)
    chunks = chunk_document(text)
    log.info("  Split into %d chunks.", len(chunks))

    collection = get_collection(chroma_path)

    # Prepare batch data.
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []

    for idx, chunk in enumerate(chunks):
        chunk_id = f"{content_hash}_{idx:05d}"
        meta: dict[str, str] = {
            "source_file": file_path.name,
            "document_type": doc_type,
            "section": chunk["section"] or "unknown",
            "chunk_index": str(idx),
            "content_hash": content_hash,
        }
        if aircraft_type:
            meta["aircraft_type"] = aircraft_type

        ids.append(chunk_id)
        documents.append(chunk["text"])
        metadatas.append(meta)

    # Generate embeddings.
    log.info("  Generating embeddings …")
    embeddings = embedder.embed(documents)

    # Upsert into ChromaDB (handles duplicates via deterministic IDs).
    batch_size = 256
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i : i + batch_size],
            embeddings=embeddings[i : i + batch_size],
            documents=documents[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
        )

    log.info("  Stored %d chunks in ChromaDB.", len(ids))
    return len(ids)


def ingest_directory(
    dir_path: Path,
    doc_type: str,
    aircraft_type: Optional[str],
    chroma_path: str,
    embedder: EmbeddingProvider,
) -> int:
    """Walk a directory and ingest all supported files."""
    total = 0
    for root, _dirs, files in os.walk(dir_path):
        for fname in sorted(files):
            fpath = Path(root) / fname
            if fpath.suffix.lower() in SUPPORTED_EXTENSIONS:
                total += ingest_file(fpath, doc_type, aircraft_type, chroma_path, embedder)
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Ingest documents into the Super Hornet context store.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--file",
        type=Path,
        help="Path to a single file to ingest (PDF, TXT, or MD).",
    )
    source.add_argument(
        "--dir",
        type=Path,
        help="Path to a directory — all supported files will be ingested.",
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=VALID_DOC_TYPES,
        dest="doc_type",
        help="Document type tag for metadata filtering.",
    )
    parser.add_argument(
        "--aircraft",
        type=str,
        default=None,
        help='Aircraft type tag, e.g. "Cessna 172" or "Boeing 747-8".',
    )
    parser.add_argument(
        "--chroma-path",
        type=str,
        default=DEFAULT_CHROMA_PATH,
        help=f"Path to ChromaDB persistent storage (default: {DEFAULT_CHROMA_PATH}).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    embedder = EmbeddingProvider()

    if args.file:
        if not args.file.exists():
            log.error("File not found: %s", args.file)
            sys.exit(1)
        if args.file.suffix.lower() not in SUPPORTED_EXTENSIONS:
            log.error("Unsupported file type: %s (supported: %s)", args.file.suffix, SUPPORTED_EXTENSIONS)
            sys.exit(1)
        total = ingest_file(args.file, args.doc_type, args.aircraft, args.chroma_path, embedder)
    else:
        if not args.dir.is_dir():
            log.error("Directory not found: %s", args.dir)
            sys.exit(1)
        total = ingest_directory(args.dir, args.doc_type, args.aircraft, args.chroma_path, embedder)

    log.info("Done — %d total chunks ingested.", total)


if __name__ == "__main__":
    main()
