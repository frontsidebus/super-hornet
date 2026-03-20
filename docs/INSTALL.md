# MERLIN -- Local Installation Guide

This guide walks you through setting up MERLIN on a Windows machine with MSFS 2024. By the end, you will have the SimConnect bridge reading telemetry, Docker services running Whisper and ChromaDB, and the orchestrator ready to accept voice or text input.

---

## Prerequisites

### Hardware & OS

- **Windows 10 (build 19041+) or Windows 11** -- required for MSFS 2024 and WSL2.
- **Microphone** -- for voice input to Whisper STT.
- **Speakers or headset** -- for MERLIN's TTS output via ElevenLabs.
- **GPU (optional)** -- an NVIDIA GPU with CUDA support accelerates Whisper transcription significantly. Not required; CPU inference works but is slower.

### Software

| Requirement | Version | Notes |
|---|---|---|
| [Microsoft Flight Simulator 2024](https://www.xbox.com/games/microsoft-flight-simulator-2024) | Latest | Steam or Microsoft Store |
| MSFS 2024 SDK | Bundled with sim | See installation steps below |
| [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0) | 8.0+ | For building the SimConnect bridge |
| [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) | Latest | With WSL2 backend enabled |
| [Git](https://git-scm.com/downloads) | Any recent | For cloning the repository |
| [Python 3.11+](https://www.python.org/downloads/) | 3.11 or 3.12 | Only needed if running the orchestrator outside Docker |

### API Keys

You need two API keys:

| Service | Purpose | Where to get it |
|---|---|---|
| **Anthropic** | Claude inference (the AI brain) | [console.anthropic.com](https://console.anthropic.com/) -- sign up, create an API key under Settings > API Keys |
| **ElevenLabs** | Text-to-speech (MERLIN's voice) | [elevenlabs.io](https://elevenlabs.io/) -- sign up, find your API key in Profile > API Keys |

ElevenLabs is optional if you only want text-mode interaction. Anthropic is required.

---

## Step-by-Step Installation

### 1. Clone the Repository

```bash
git clone https://github.com/frontsidebus/airdale.git
cd airdale
```

### 2. Install and Configure Docker Desktop

1. Download and install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/).
2. During setup, ensure **Use WSL 2 based engine** is selected.
3. After installation, open Docker Desktop and verify it is running (whale icon in the system tray).
4. In Docker Desktop Settings > Resources > WSL Integration, enable integration with your default WSL distro if you plan to run commands from WSL.

Verify Docker is working:

```bash
docker --version
docker compose version
```

### 3. Install the MSFS 2024 SDK

The SimConnect bridge requires `Microsoft.FlightSimulator.SimConnect.dll` from the MSFS SDK.

1. Launch MSFS 2024.
2. Go to **Options > General > Developers**.
3. Enable **Developer Mode**.
4. In the Developer menu bar that appears at the top of the sim, click **Help > SDK Installer**.
5. Run the installer and note the install location. Typical paths:
   - `C:\MSFS SDK\`
   - `C:\MSFS2024SDK\`
6. After installation, confirm this file exists:
   ```
   C:\MSFS SDK\SimConnect SDK\lib\managed\Microsoft.FlightSimulator.SimConnect.dll
   ```

### 4. Build the SimConnect Bridge

Open a terminal (PowerShell, CMD, or Windows Terminal) and navigate to the bridge directory:

```bash
cd simconnect-bridge
```

**Check the SimConnect DLL path.** Open `SimConnectBridge.csproj` and verify the `HintPath` matches your SDK installation:

```xml
<Reference Include="Microsoft.FlightSimulator.SimConnect">
  <HintPath>C:\MSFS SDK\SimConnect SDK\lib\managed\Microsoft.FlightSimulator.SimConnect.dll</HintPath>
  <Private>true</Private>
</Reference>
```

If your SDK installed to a different location (e.g., `C:\MSFS2024SDK\`), update the path accordingly.

Build the project:

```bash
dotnet restore
dotnet build
```

You should see `Build succeeded` with no errors. If you get a missing assembly error for `Microsoft.FlightSimulator.SimConnect`, the HintPath is incorrect.

### 5. Configure Environment Variables

From the repository root:

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the required values:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Required for voice output (optional for text-only mode)
ELEVENLABS_API_KEY=your-key-here
ELEVENLABS_VOICE_ID=your-voice-id-here
```

**Finding an ElevenLabs voice ID:** Log in to [elevenlabs.io](https://elevenlabs.io/), go to **Voices**, select a voice, and copy the Voice ID from the voice details panel. Voices with a "conversational" or "narrative" style work well for MERLIN.

All other settings have sensible defaults. See the comments in `.env.example` for the full list of options.

### 6. Start Docker Services

From the repository root:

```bash
docker compose up -d
```

This starts three services:

| Service | Container | Port | Purpose |
|---|---|---|---|
| Whisper | `merlin-whisper` | 9000 | Local speech-to-text via HTTP API |
| ChromaDB | `merlin-chromadb` | 8000 | Vector store for RAG document retrieval |
| Orchestrator | `merlin-orchestrator` | 8081 | MERLIN's brain (optional -- you can also run it natively) |

The first startup will pull Docker images and download the Whisper model. This can take several minutes depending on your connection speed and the model size.

**For development**, use the dev override to get faster startup (tiny Whisper model) and hot-reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### 7. Verify Services Are Running

**Check container status:**

```bash
docker compose ps
```

All services should show `Up` or `Up (healthy)`.

**Check Whisper health:**

```bash
curl http://localhost:9000/docs
```

You should get an HTML page (the Swagger docs). If the endpoint is not yet ready, Whisper is still downloading the model -- check logs:

```bash
docker compose logs -f whisper
```

**Check ChromaDB health:**

```bash
curl http://localhost:8000/api/v1/heartbeat
```

Expected response: `{"nanosecond heartbeat": <timestamp>}`

### 8. Ingest Your First Flight Manual

Place a text file (aircraft POH, procedures manual, or similar) in the `data/` directory, then use the context store to ingest it. From the orchestrator environment:

```bash
cd orchestrator
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -e .

python -c "
import asyncio
from orchestrator.context_store import ContextStore

async def main():
    store = ContextStore('./data/chromadb')
    count = await store.ingest_document(
        'data/your-manual.txt',
        metadata={'aircraft_type': 'Cessna 172'}
    )
    print(f'Ingested {count} chunks')

asyncio.run(main())
"
```

The store accepts plain text files. PDF and other formats should be converted to text first.

### 9. Download FAA Data

MERLIN uses the public [Aviation API](https://api.aviationapi.com/) for airport lookups. No separate data download is required -- lookups are made on demand over HTTP. Airport data is cached locally based on the `AIRPORT_CACHE_TTL` setting (default: 1 hour).

### 10. Run the Orchestrator

You have two options for running the orchestrator:

**Option A: Inside Docker (recommended)**

If you started all services with `docker compose up -d`, the orchestrator is already running. Attach to it:

```bash
docker attach merlin-orchestrator
```

**Option B: Natively on your machine**

This is useful during development or if you need direct microphone access for voice input:

```bash
cd orchestrator
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
merlin
```

When running natively, make sure your `.env` file points to the correct service URLs:

```bash
WHISPER_URL=http://localhost:9000
SIMCONNECT_BRIDGE_URL=ws://localhost:8080
```

You should see:

```
=== MERLIN AI Co-Pilot ===
Type your message, or 'voice' to toggle voice input.
Commands: /voice, /vad, /ptt, /capture, /clear, /quit
```

MERLIN is ready.

---

## Troubleshooting

### SimConnect Connection Failures

**Symptom:** `Could not connect to SimConnect bridge` or `COMException` when starting the bridge.

- Ensure MSFS 2024 is running **before** you start the SimConnect bridge.
- Verify Developer Mode is enabled in MSFS (Options > General > Developers).
- Confirm the SimConnect DLL path in `SimConnectBridge.csproj` is correct.
- If you recently updated MSFS, the SDK may need reinstalling. Run the SDK Installer again from the Developer menu.
- The bridge retries the connection every 5 seconds by default. You can start it before MSFS and let it wait.

**Symptom:** Bridge connects but no telemetry appears.

- You must be in an active flight (free flight, bush trip, etc.). The main menu does not expose SimConnect data.
- Check that you're running the bridge as a native Windows process, not inside WSL or Docker. SimConnect requires native Windows COM interop.

### Docker Networking (WSL2 / Windows)

**Symptom:** Orchestrator in Docker cannot reach the SimConnect bridge on `localhost:8080`.

The SimConnect bridge runs on the Windows host, not in Docker. Inside Docker, use `host.docker.internal` instead of `localhost`. The provided `docker-compose.yml` already configures this:

```yaml
environment:
  - SIMCONNECT_BRIDGE_URL=ws://host.docker.internal:8080
extra_hosts:
  - "host.docker.internal:host-gateway"
```

If you are running the orchestrator natively (outside Docker), use `ws://localhost:8080`.

**Symptom:** Cannot reach Whisper or ChromaDB from the host.

- Verify ports 9000 and 8000 are not in use by another process: `netstat -ano | findstr :9000`
- Check Docker Desktop firewall settings.
- Restart Docker Desktop if port mappings are not working after a system sleep/wake.

### Whisper Model Download Takes Too Long

The default model (`base`) is ~150 MB. Larger models (`medium`, `large-v3`) can be over 3 GB.

- On first start, Whisper downloads the model inside the container. Check progress with `docker compose logs -f whisper`.
- The model is cached in a Docker volume (`whisper_cache`), so subsequent starts are fast.
- For faster development iteration, use the dev override which sets the model to `tiny` (~75 MB): `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`
- If behind a corporate proxy, configure Docker Desktop's proxy settings under Settings > Resources > Proxies.

### ChromaDB Permission Errors

**Symptom:** `PermissionError` or `OSError` when ChromaDB tries to write to `./data/chroma_db`.

- The ChromaDB container mounts `./data/chroma_db` from the host. Ensure this directory exists and is writable:
  ```bash
  mkdir -p data/chroma_db
  ```
- On Linux/WSL, check that the directory permissions allow the container's user to write:
  ```bash
  chmod -R 777 data/chroma_db
  ```

### Audio Device Issues

**Symptom:** `sounddevice.PortAudioError` or no microphone input.

- The orchestrator uses `sounddevice` (PortAudio) for microphone access. This requires running **natively on Windows**, not inside Docker or WSL, unless you have PulseAudio forwarding configured.
- List available audio devices: `python -c "import sounddevice; print(sounddevice.query_devices())"`
- Set a specific input device in `.env`: `AUDIO_INPUT_DEVICE=1` (use the device index from the list above).
- If you only want text-mode interaction, audio device issues do not matter. Just type at the `Captain>` prompt.

### Common Error Messages

| Error | Cause | Fix |
|---|---|---|
| `anthropic.AuthenticationError` | Invalid or missing Anthropic API key | Check `ANTHROPIC_API_KEY` in `.env` |
| `ConnectionRefusedError: [Errno 111] Connection refused` (ws://localhost:8080) | SimConnect bridge is not running | Start the bridge first: `cd simconnect-bridge && dotnet run` |
| `httpx.ConnectError` to port 9000 | Whisper container is not running or still starting | Run `docker compose up -d whisper` and wait for healthy status |
| `chromadb.errors.NoIndexException` | ChromaDB has no documents ingested yet | Ingest at least one document (see step 8) |
| `FileNotFoundError: SimConnect.dll` | .NET build cannot find the SimConnect DLL | Update the HintPath in `SimConnectBridge.csproj` |
