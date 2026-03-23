# CLAUDE.md -- Project Conventions for Super Hornet

## Project Overview

**Super Hornet** is an AI agent platform for **Star Citizen**. Forked from MERLIN (an AI copilot for MSFS 2024), it uses the **Constellation** architecture — three decoupled layers (Perception, Reasoning, Action) that enable an AI wingman to understand game state, reason about it, and optionally act on the pilot's behalf.

Star Citizen has no official API and uses Easy Anti-Cheat (EAC). All game state must be inferred from `game.log` parsing and screen capture (vision). Actions are performed via OS-level input simulation (EAC-safe).

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestrator | Python 3.11+ (async, hatch build system) |
| Web Server | FastAPI with WebSocket support (browser UI) |
| AI Inference | Anthropic Claude API with tool use |
| Vector Store / RAG | ChromaDB (knowledge base + skill library) |
| Speech-to-Text | faster-whisper (CTranslate2) `medium` model via Docker |
| Text-to-Speech | ElevenLabs streaming API (`eleven_multilingual_v2` model) |
| Config | pydantic-settings with .env files |
| Perception | game.log parser + Claude Vision (screen capture with ROI) |
| External APIs | UEX Corp API 2.0 (trade data), Star Citizen Wiki API |
| Input Simulation | PyDirectInput (EAC-safe, behind feature flag) |

## Directory Structure

```
super-hornet/
├── orchestrator/                # Python package -- the brain
│   ├── orchestrator/            # Source package
│   │   ├── __init__.py
│   │   ├── audio_processing.py  # Audio preprocessing (high-pass, trim, normalize)
│   │   ├── claude_client.py     # Anthropic API wrapper with Super Hornet persona + tools
│   │   ├── config.py            # Pydantic settings from .env
│   │   ├── context_store.py     # ChromaDB RAG store with query cache
│   │   ├── game_activity.py     # State-machine game activity detector
│   │   ├── game_client.py       # Aggregates state from perception modules
│   │   ├── game_state.py        # Star Citizen game state models (GameState, ShipStatus, etc.)
│   │   ├── health.py            # Health monitoring infrastructure
│   │   ├── input_simulator.py   # PyDirectInput wrapper (EAC-safe input simulation)
│   │   ├── log_parser.py        # Real-time game.log parser
│   │   ├── log_patterns.py      # Regex patterns for game.log event extraction
│   │   ├── main.py              # CLI entry point
│   │   ├── sc_wiki_client.py    # Star Citizen Wiki API client
│   │   ├── screen_capture.py    # Screen capture with ROI support for vision
│   │   ├── skill_library.py     # Voyager-inspired learned action sequence store
│   │   ├── tools.py             # Claude tool implementations (SC-specific)
│   │   ├── tts_preprocessor.py  # Star Citizen text preprocessing for TTS
│   │   ├── uex_client.py        # UEX Corp API 2.0 client (trade data)
│   │   ├── vision.py            # Vision module (screen capture + Claude Vision)
│   │   ├── voice.py             # Voice I/O (PTT, VAD, barge-in, streaming TTS)
│   │   └── whisper_client.py    # Whisper ASR HTTP client with retry logic
│   ├── tests/                   # Unit tests (pytest + pytest-asyncio)
│   ├── Dockerfile
│   └── pyproject.toml           # Build config, dependencies, ruff settings
├── web/                         # FastAPI web UI server
│   ├── server.py                # Backend: game state WS, chat, STT/TTS proxy
│   ├── run.py                   # Dev server launcher
│   ├── requirements.txt
│   └── static/                  # Browser frontend
│       ├── index.html           # Star Citizen HUD overlay
│       ├── app.js               # WebSocket client, audio capture, UI logic
│       └── style.css
├── data/
│   ├── checklists/              # YAML procedure files (ship startup, combat, mining, etc.)
│   ├── prompts/                 # System prompt templates
│   │   ├── hornet_system.md     # Super Hornet persona definition
│   │   └── hornet_combat.md     # Combat-specific prompt overlay
│   └── vision_rois.yaml         # HUD region-of-interest definitions per resolution
├── tests/                       # Integration tests (root level)
│   └── integration/
├── tools/                       # Developer utilities
│   ├── download_sc_data.py      # Fetch UEX + Wiki data for knowledge base seeding
│   ├── ingest.py                # Document ingestion into ChromaDB
│   └── test_tts.py              # ElevenLabs TTS smoke test
├── docs/                        # Project documentation
│   ├── ARCHITECTURE.md          # Constellation architecture and data flows
│   ├── GETTING_STARTED.md
│   └── INSTALL.md
├── docker-compose.yml           # Production service stack
├── docker-compose.dev.yml       # Dev overrides (hot-reload, bind mounts)
├── .env.example                 # Environment variable template
├── .env                         # Local config (git-ignored)
└── CLAUDE.md                    # This file
```

## Development Commands

### Python Orchestrator

```bash
cd orchestrator

# Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Run the orchestrator (CLI mode)
hornet

# Lint
ruff check .

# Format
ruff format .

# Run tests
pytest
```

### Web UI Server

```bash
cd web

# Install dependencies (or use the orchestrator venv)
pip install -r requirements.txt

# Run the FastAPI dev server (defaults to http://localhost:3838)
python run.py
```

### Docker Services

```bash
# Start all services (Whisper, ChromaDB, orchestrator)
docker compose up -d

# Dev mode with hot-reload and bind mounts
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# View logs
docker compose logs -f orchestrator

# Rebuild after dependency changes
docker compose build --no-cache orchestrator
```

## Code Style

### Python

- **Linter/Formatter:** ruff (config in `pyproject.toml`)
- **Line length:** 100 characters
- **Type hints:** Required on all function signatures
- **Async:** Use `async`/`await` throughout the orchestrator -- the event loop is the heartbeat
- **Imports:** Sorted by ruff (isort-compatible)
- **Naming:** `snake_case` for functions and variables, `PascalCase` for classes
- **Models:** Use Pydantic `BaseModel` for all data structures crossing boundaries
- **Config:** Use `pydantic-settings` `BaseSettings` -- never hardcode keys or magic numbers
- **ruff rules enabled:** E (pycodestyle), F (pyflakes), I (isort), N (pep8-naming), UP (pyupgrade), B (bugbear), SIM (simplify)

## Important Architectural Decisions

1. **No SimConnect bridge** -- Star Citizen has no equivalent API. The perception layer replaces it entirely with Python modules (log parsing + vision).

2. **Three-layer Constellation architecture** -- Perception (data in), Reasoning (Claude + tools), Action (data/commands out). Each layer is decoupled via async interfaces.

3. **Game state is best-effort** -- Unlike MERLIN's SimState which receives structured telemetry at 20Hz, GameState is composed from unreliable sources. All fields carry implicit confidence.

4. **EAC-safe only** -- No memory reading, no DLL injection, no game file modification. Only screen capture (out-of-process), log tailing, and OS-level input simulation.

5. **Input simulation behind feature flag** -- `INPUT_SIMULATION_ENABLED=false` by default. All simulated inputs are logged. Dangerous actions require explicit confirmation.

6. **Vision pipeline with ROI cropping** -- Instead of sending full screenshots to Claude Vision, crop specific HUD regions (shields, radar, fuel, QT status) to reduce cost and latency.

7. **Skill library (Voyager-inspired)** -- Learned action sequences stored in ChromaDB. When the agent successfully executes a multi-step operation, it saves the sequence as a reusable "skill" with verification tracking.

8. **Dual ChromaDB collections** -- Knowledge base (`hornet_knowledge`) for RAG on ship manuals, trade data, lore. Skill library (`hornet_skills`) for learned action sequences.

9. **Activity-aware response styles** -- Each GameActivity injects a style directive into Claude's system prompt (COMBAT = ultra-brief tactical, QUANTUM_TRAVEL = conversational, MINING = technical).

10. **Dynamic token budgeting** -- 256 tokens for acknowledgments, 1024 for routine, 2048 for briefings/trade plans.

11. **Voice is streaming** -- TTS begins playing as Claude's response streams in. Barge-in support cancels in-flight responses immediately.

12. **Audio preprocessing pipeline** -- High-pass filter, silence trimming, normalization. Star Citizen vocabulary prompt biases Whisper toward game terminology.

13. **External API integration** -- UEX Corp API for live trade data (80+ endpoints), Star Citizen Wiki API for lore and ship specs.

14. **Game.log is the primary real-time data source** -- Parsed with regex patterns for kills, deaths, location changes, QT events, crime stat changes.

## Testing Approach

- **Python:** pytest + pytest-asyncio for async tests
- **Mock perception modules in unit tests** -- use JSON fixtures for game state
- **Test categories:** unit tests (game state, log parser, vision, tools, Claude client, config), integration tests (orchestrator E2E, tool chain, Whisper pipeline)
- **No game required for most tests** -- record game.log snippets and screenshots as fixtures

## Environment Variables

All config flows through `.env` files loaded by `pydantic-settings`. See `.env.example` for the complete list with documentation. Never commit `.env` to version control.
