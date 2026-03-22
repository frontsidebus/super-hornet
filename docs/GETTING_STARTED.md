# Getting Started with MERLIN

This guide assumes you have completed the [installation](INSTALL.md). You have the SimConnect bridge built, Docker services running, and your `.env` configured with API keys.

---

## Your First Flight with MERLIN

### 1. Start MSFS 2024

Launch MSFS 2024 and load into a **Free Flight**. For your first session, pick something familiar:

- **Aircraft:** Cessna 172 Skyhawk (simple, well-modeled, single engine)
- **Airport:** Anywhere you know -- your home airport, or a big field like KLAX or KJFK
- **Weather:** Clear skies (to keep things simple)
- **Time:** Daytime

Wait until you are fully loaded into the cockpit before proceeding.

### 2. Start MERLIN

The easiest way to start everything is with the startup script (from WSL):

```bash
./scripts/start.sh
```

This launches Docker services (Whisper, ChromaDB), builds and starts the SimConnect bridge, and starts the web server. You'll see a status summary when everything is ready.

To stop all components:

```bash
./scripts/stop.sh
```

<details>
<summary>Manual startup (step-by-step)</summary>

**Start the SimConnect Bridge** -- open a terminal on your Windows host (PowerShell or CMD):

```bash
cd simconnect-bridge
dotnet run
```

You should see:

```
=== MERLIN SimConnect Bridge ===

[Bridge] Attempting SimConnect connection as "MERLIN SimConnect Bridge"...
[SimConnect] Connection opened.
[SimConnect] Data definitions registered.
[WebSocket] Server started on ws://0.0.0.0:8080
[SimConnect] Polling started: high-freq=30Hz, low-freq=1Hz
[Bridge] SimConnect connected. Broadcasting telemetry.
[Bridge] Press Ctrl+C to shut down.
```

If you see `Retrying in 5000ms...`, make sure MSFS is fully loaded into a flight (not the main menu).

**Start Docker Services:**

```bash
docker compose up -d
```

**Start the Web Server:**

```bash
cd web
source ../orchestrator/.venv/bin/activate
python run.py
```

</details>

### 5. Verify Connection

When everything is connected, you will see:

```
=== MERLIN AI Co-Pilot ===
Type your message, or 'voice' to toggle voice input.
Commands: /voice, /vad, /ptt, /capture, /clear, /quit

Captain>
```

Run `/status` to confirm connectivity:

```
Captain> /status
SimConnect: Connected | Phase: PREFLIGHT | Alt: 433ft | IAS: 0kt | HDG: 270° | VS: +0fpm
Docs in store: 0
Screen capture: off
```

If SimConnect shows "Not connected", check that the bridge is running and the WebSocket URL in your `.env` is correct.

### 6. Talk to MERLIN

Type a message at the `Captain>` prompt:

```
Captain> MERLIN, what's our current altitude and airspeed?
```

MERLIN will call the `get_sim_state` tool, read your telemetry, and respond with your current flight parameters.

**Example prompts to try:**

| Prompt | What MERLIN does |
|---|---|
| `What's our current altitude and airspeed?` | Reads live telemetry and reports |
| `Run me through the before-takeoff checklist` | Retrieves the phase-appropriate checklist |
| `Brief me on the approach into KJFK` | Looks up airport data and builds an approach briefing |
| `What's the weather looking like?` | Reports ambient conditions from sim telemetry |
| `Tell me about the engine instruments` | Reads engine RPM, oil temp/pressure, fuel flow |
| `Create a flight plan from KLAX to KSFO at 8000 feet` | Builds a draft flight plan with airport lookups |
| `Search the manual for Vne` | Queries the RAG store for aircraft limitations |

### 7. Text Mode vs Voice Mode

**Text mode** (default): Type at the `Captain>` prompt. Best for testing or noisy environments.

**Voice mode**: Toggle with the `/voice` command. MERLIN listens through your microphone, transcribes via Whisper, processes with Claude, and responds through ElevenLabs TTS.

```
Captain> /voice
Voice input toggled.
[Listening...]
```

The default voice input mode is push-to-talk. Switch between modes:

- `/ptt` -- Push-to-talk mode (press Enter to stop recording)
- `/vad` -- Voice activity detection (speaks, pauses, and MERLIN responds automatically)

### 8. Slash Commands Reference

| Command | Description |
|---|---|
| `/voice` | Toggle voice input on/off |
| `/vad` | Switch to voice-activity-detection mode |
| `/ptt` | Switch to push-to-talk mode |
| `/capture` | Toggle screen capture for Claude Vision analysis |
| `/status` | Show connection status, document count, and capture state |
| `/clear` | Clear conversation history (start fresh) |
| `/quit` | Shut down MERLIN |

---

## Ingesting Flight Manuals

MERLIN's RAG pipeline lets you load aircraft POHs and reference documents so it can answer aircraft-specific questions with real data instead of general knowledge.

### Supported Formats

The context store ingests **plain text files** (`.txt`). If you have a PDF manual, convert it to text first using a tool like `pdftotext`, Adobe Acrobat, or an online converter.

### How to Ingest a Document

```python
import asyncio
from orchestrator.context_store import ContextStore

async def main():
    store = ContextStore("./data/chromadb")
    count = await store.ingest_document(
        "data/manuals/c172s_poh.txt",
        metadata={"aircraft_type": "Cessna 172 Skyhawk"},
        chunk_size=1000,    # characters per chunk
        chunk_overlap=200,  # overlap between chunks
    )
    print(f"Ingested {count} chunks")

asyncio.run(main())
```

The `aircraft_type` metadata field is important -- it allows MERLIN to filter searches to the aircraft you are currently flying.

### Verifying Ingestion

Use `/status` in the orchestrator to check the document count:

```
Captain> /status
Docs in store: 47
```

Or query the store directly:

```python
results = await store.query("Vne limitations", n_results=3)
for r in results:
    print(r["metadata"]["source"], "-", r["content"][:100])
```

### Tips for Good Results

- **One document per file.** Don't merge multiple manuals into one text file.
- **Include section headers.** The chunking algorithm works better when text has clear structure.
- **Set the `aircraft_type` metadata** to match how MSFS reports the aircraft title (e.g., `"Cessna 172 Skyhawk G1000"`).
- **Chunk size of 1000 characters** (the default) works well for most manuals. Increase to 1500-2000 for dense technical content.

---

## Customizing MERLIN

### Adjusting the Persona

MERLIN's system prompt is defined in two places:

- **Full prompt:** `data/prompts/merlin_system.md` -- the complete persona definition with behavioral guidelines, knowledge scope, and tool usage rules. This is the reference document.
- **Runtime prompt:** `orchestrator/orchestrator/claude_client.py` -- the `MERLIN_PERSONA` constant that is actually sent to Claude on each request. Edit this to change MERLIN's personality, tone, or behavioral rules.

For example, to make MERLIN more formal and less humorous, adjust the personality bullet points in the persona string.

### Adding Custom Checklists

Checklists are YAML files stored in `data/checklists/`. Two generic checklists are included:

- `generic_single_engine.yaml` -- for Cessna 172, PA-28, SR22, and similar
- `generic_jet.yaml` -- for 747, 787, A320, CJ4, and similar

**YAML format:**

```yaml
aircraft_class: single_engine_piston
version: "1.0"
author: MERLIN

phases:
  preflight:
    name: "Preflight Inspection"
    items:
      - item: "Weather briefing"
        setting: "Obtained"
        remark: "Optional MERLIN commentary."
      - item: "Fuel quantity"
        setting: "Sufficient for flight + reserves"
        remark: ~   # tilde means no remark
```

Each phase contains a list of items with:
- `item` -- the checklist action
- `setting` -- the expected position or value
- `remark` -- optional MERLIN commentary (set to `~` for none)

**Phase names used in the checklists:**

`preflight`, `before_start`, `engine_start`, `before_taxi`, `before_takeoff`, `takeoff`, `climb`, `cruise`, `descent`, `approach`, `before_landing`, `after_landing`, `shutdown`

To add a checklist for a new aircraft, create a new YAML file following the same format and ingest it into the context store.

### Configuring Voice

**Whisper model size** -- controls transcription speed and accuracy:

| Model | Size | Speed | Accuracy | Set in `.env` |
|---|---|---|---|---|
| `tiny.en` | 75 MB | Fastest | Lowest | Good for development |
| `base.en` | 150 MB | Fast | Good | Default -- recommended |
| `small.en` | 500 MB | Moderate | Better | Good balance |
| `medium.en` | 1.5 GB | Slow | High | For quiet environments |
| `large-v3` | 3 GB | Slowest | Highest | Best accuracy, needs GPU |

Set via `WHISPER_MODEL` in `.env` or the `ASR_MODEL` environment variable in `docker-compose.yml`.

**ElevenLabs voice** -- choose any voice from your ElevenLabs library. Set `ELEVENLABS_VOICE_ID` in `.env`. Voices with a "narrative" or "conversational" style suit MERLIN's personality. You can also tune `ELEVENLABS_STABILITY`, `ELEVENLABS_SIMILARITY_BOOST`, and `ELEVENLABS_STYLE` in `.env`.

### Enabling Screen Capture

Screen capture sends MSFS screenshots to Claude Vision for instrument and environment reads. It is disabled by default.

```bash
# In .env
SCREEN_CAPTURE_ENABLED=true
SCREEN_CAPTURE_INTERVAL=2.0   # seconds between captures
```

Or toggle it at runtime:

```
Captain> /capture
Screen capture enabled.
```

Screen capture works best when running the orchestrator natively on Windows (not inside Docker or WSL), since it uses `mss` to grab the primary monitor.

---

## Feature Roadmap

### What Works Now (Phases 1-3)

- SimConnect telemetry streaming over WebSocket
- Text and voice conversation with MERLIN via Claude
- Tool use: `get_sim_state`, `lookup_airport`, `search_manual`, `get_checklist`, `create_flight_plan`
- RAG document ingestion and retrieval via ChromaDB
- FAA airport lookups via the Aviation API
- Push-to-talk and voice-activity-detection input modes
- ElevenLabs TTS output with streaming playback
- Docker Compose deployment for Whisper, ChromaDB, and the orchestrator

### Coming Soon (Phases 4-6)

**Phase 4 -- Situational Awareness:**
- Automatic flight phase detection from telemetry (the algorithm is implemented; proactive callouts are next)
- Phase-triggered checklist prompts without being asked
- Altitude, speed, and configuration deviation alerts

**Phase 5 -- Vision:**
- Screen capture pipeline sending frames to Claude Vision
- MERLIN reads instruments and observes the visual environment
- Selective activation during critical flight phases to manage API costs

**Phase 6 -- Flight Planning:**
- Full route building with airway and waypoint selection
- Fuel calculations based on aircraft performance data
- Weather integration for route planning
- End-to-end mission support from preflight planning to shutdown debrief
