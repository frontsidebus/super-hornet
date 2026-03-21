# MERLIN

**AI Co-Pilot for Microsoft Flight Simulator 2024**

MERLIN is a voice-interactive AI co-pilot powered by [Claude](https://www.anthropic.com/claude) that connects to MSFS 2024 via SimConnect. It provides real-time flight guidance, checklist management, and situational awareness through a browser-based cockpit UI -- delivered with the personality of a Navy Test Pilot who has seen it all.

> *"Airdale" is Navy slang for a naval aviator. Fitting, because MERLIN flies right seat.*

---

## Architecture

```
                                          +-------------+
                                          |   Claude    |
                                          |    API      |
                                          +------+------+
                                                 |
+----------+   SimConnect   +------------+  WS   +------------+  HTTP  +-----------+
|          |<-------------->|  SimConnect |<------>|    Web     |<------>|  Browser  |
| MSFS 2024|  (telemetry)   |   Bridge   |        |   Server   |        |    UI     |
|          |                |  (C# .NET) |        |  (FastAPI) |        | (Cockpit) |
+----------+                +------------+        +-----+------+        +-----------+
                                                  |     |      |
                                          +-------+ +---+----+ +--------+
                                          |         |        |          |
                                     +----+---+ +---+----+ +-+-------+ |
                                     | Whisper | | Chroma | |Eleven   | |
                                     |  STT    | |   DB   | | Labs    | |
                                     | (Docker)| | (Docker)| | TTS    | |
                                     +---------+ +--------+ +---------+ |
```

---

## Features

- **Live telemetry** -- airspeed, altitude, attitude, engine params, and control surfaces streamed in real time
- **Voice input/output** -- Whisper STT with aviation vocabulary prompting and ElevenLabs streaming TTS
- **AI co-pilot with flight-phase awareness** -- automatic phase detection (preflight through rollout) drives checklists and proactive callouts
- **Barge-in interruption** -- speak or type while MERLIN is responding to cancel and redirect
- **Push-to-talk and VAD modes** -- toggle between voice activation and manual push-to-talk
- **RAG-based manual lookup** -- aircraft POH and procedures chunked and embedded in ChromaDB for instant retrieval
- **Auto-reconnect** -- graceful degradation and automatic reconnection to SimConnect, Whisper, and ChromaDB
- **Browser-based cockpit UI** -- TARS-style display with telemetry gauges, chat, and audio controls

---

## Quick Start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0) (Windows, for the SimConnect bridge)
- Microsoft Flight Simulator 2024 with the SDK installed
- API keys: [Anthropic](https://console.anthropic.com/) (required), [ElevenLabs](https://elevenlabs.io/) (required for voice)

### 1. Clone and configure

```bash
git clone https://github.com/frontsidebus/airdale.git
cd airdale
cp .env.example .env
# Edit .env with your API keys (ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID)
```

### 2. Start Docker services

```bash
docker compose up -d
```

This launches Whisper (STT), ChromaDB (RAG), and the web server. First startup downloads the Whisper `small` model -- allow a few minutes.

### 3. Start the SimConnect bridge (Windows)

```bash
cd simconnect-bridge
dotnet restore && dotnet build
dotnet run
```

The bridge connects to MSFS and streams telemetry over WebSocket on port 8080.

### 4. Open the cockpit UI

Navigate to [http://localhost:3838](http://localhost:3838) in your browser. MERLIN is ready.

> **WSL2 note:** If running Docker in WSL2, set `SIMCONNECT_WS_HOST` in `.env` to your Windows host IP (not `localhost`). See [docs/INSTALL.md](docs/INSTALL.md) for details.

---

## Tech Stack

| Layer | Technology |
|---|---|
| SimConnect Bridge | C# / .NET 8 (out-of-process) |
| Web Server | Python 3.11+ / FastAPI |
| AI Inference | Anthropic Claude API with tool use |
| Speech-to-Text | Whisper (local, via Docker) |
| Text-to-Speech | ElevenLabs streaming API |
| Vector Store / RAG | ChromaDB with sentence-transformers |
| IPC | WebSocket (JSON) |
| Frontend | HTML/JS cockpit display |
| Config | pydantic-settings with .env |

---

## UI

<!-- TODO: Add screenshot of the cockpit UI -->

The browser UI features a TARS-style cockpit display with:
- Real-time telemetry gauges (airspeed, altitude, heading, vertical speed)
- Chat panel with conversation history
- Audio controls (push-to-talk, VAD toggle, volume)
- Flight phase indicator
- Connection status for all subsystems

---

## Project Structure

```
airdale/
├── web/                    # FastAPI web server and browser UI
│   ├── server.py           # Backend: telemetry WS, chat, STT/TTS
│   └── static/             # Frontend: cockpit display
├── orchestrator/           # Python orchestration package
│   ├── orchestrator/       # Source: config, sim_client, claude_client, voice, etc.
│   └── pyproject.toml      # Build config (hatch + ruff)
├── simconnect-bridge/      # C# .NET SimConnect bridge (runs on Windows)
├── docker-compose.yml      # Whisper, ChromaDB, orchestrator services
├── .env.example            # Environment variable template
└── docs/
    └── INSTALL.md          # Detailed installation guide
```

---

## Documentation

- [Installation Guide](docs/INSTALL.md) -- full setup walkthrough with troubleshooting
- [Project Conventions](CLAUDE.md) -- architecture decisions, code style, development commands

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Ensure `ruff check` passes for Python code
4. Submit a pull request with a clear description

---

## License

[MIT](LICENSE) -- Copyright 2026 frontsidebus
