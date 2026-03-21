# MERLIN -- Installation Guide

This guide walks you through setting up MERLIN on a Windows machine with MSFS 2024. By the end, you will have the SimConnect bridge reading telemetry, Docker services running Whisper and ChromaDB, and the web UI serving the cockpit display in your browser.

---

## Prerequisites

### Hardware & OS

- **Windows 10 (build 19041+) or Windows 11** -- required for MSFS 2024 and WSL2.
- **Microphone** -- for voice input to Whisper STT.
- **Speakers or headset** -- for MERLIN's TTS output via ElevenLabs.
- **GPU (optional)** -- an NVIDIA GPU with CUDA support accelerates Whisper transcription. Not required; CPU inference works but is slower.

### Software

| Requirement | Version | Notes |
|---|---|---|
| [Microsoft Flight Simulator 2024](https://www.xbox.com/games/microsoft-flight-simulator-2024) | Latest | Steam or Microsoft Store |
| MSFS 2024 SDK | Bundled with sim | See installation steps below |
| [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0) | 8.0+ | For building the SimConnect bridge (Windows only) |
| [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) | Latest | With WSL2 backend enabled |
| [Git](https://git-scm.com/downloads) | Any recent | For cloning the repository |

### API Keys

| Service | Purpose | Where to get it |
|---|---|---|
| **Anthropic** | Claude inference (required) | [console.anthropic.com](https://console.anthropic.com/) -- Settings > API Keys |
| **ElevenLabs** | Text-to-speech (required for voice) | [elevenlabs.io](https://elevenlabs.io/) -- Profile > API Keys |

---

## Step-by-Step Setup

### 1. Clone the Repository

```bash
git clone https://github.com/frontsidebus/airdale.git
cd airdale
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Open `.env` and fill in the required values:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Required for voice output
ELEVENLABS_API_KEY=your-key-here
ELEVENLABS_VOICE_ID=your-voice-id-here
```

**Finding an ElevenLabs voice ID:** Log in to [elevenlabs.io](https://elevenlabs.io/), go to **Voices**, select a voice, and copy the Voice ID from the voice details panel.

All other settings have sensible defaults. See `.env.example` for the full list.

### 3. Install the MSFS 2024 SDK

The SimConnect bridge requires `Microsoft.FlightSimulator.SimConnect.dll` from the MSFS SDK.

1. Launch MSFS 2024.
2. Go to **Options > General > Developers** and enable **Developer Mode**.
3. In the Developer menu bar, click **Help > SDK Installer**.
4. Run the installer. The default install path is:
   - `C:\MSFS 2024 SDK\`
5. Confirm this file exists:
   ```
   C:\MSFS 2024 SDK\SimConnect SDK\lib\managed\Microsoft.FlightSimulator.SimConnect.dll
   ```

**Important:** After the SDK install, run the **SimConnect MSI installer** located at:
```
C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.msi
```
This registers the SimConnect COM components. You may need to re-run this MSI after major MSFS updates.

### 4. Build the SimConnect Bridge

Open a Windows terminal (PowerShell or CMD) -- the bridge must run natively on Windows, not in WSL.

```bash
cd simconnect-bridge
```

Verify the SimConnect DLL path in `SimConnectBridge.csproj` matches your SDK install:

```xml
<HintPath>C:\MSFS 2024 SDK\SimConnect SDK\lib\managed\Microsoft.FlightSimulator.SimConnect.dll</HintPath>
```

Build:

```bash
dotnet restore
dotnet build
```

You should see `Build succeeded`. If you get a missing assembly error, update the HintPath.

### 5. Start Docker Services

From the repository root:

```bash
docker compose up -d
```

This starts:

| Service | Container | Port | Purpose |
|---|---|---|---|
| Whisper | `merlin-whisper` | 9090 | Local speech-to-text (OpenAI-compatible API) |
| ChromaDB | `merlin-chromadb` | 8000 | Vector store for RAG document retrieval |
| Orchestrator | `merlin-orchestrator` | 3838 | Web server + MERLIN brain |

**First startup downloads the Whisper `small` model (~500 MB).** This can take several minutes. Monitor progress:

```bash
docker compose logs -f whisper
```

The model is cached in a Docker volume (`whisper_cache`), so subsequent starts are fast.

### 6. Verify Services

```bash
# Check all containers are running
docker compose ps

# Whisper health (should return HTML docs page)
curl http://localhost:9090/docs

# ChromaDB health
curl http://localhost:8000/api/v1/heartbeat
```

### 7. Start the SimConnect Bridge

With MSFS 2024 running, open a Windows terminal:

```bash
cd simconnect-bridge
dotnet run
```

The bridge connects to MSFS and streams telemetry over WebSocket on port 8080. It retries the connection every 5 seconds if MSFS is not yet running.

### 8. Open the Browser UI

Navigate to [http://localhost:3838](http://localhost:3838). The cockpit display will show live telemetry once the bridge connects. Use the chat panel or microphone button to interact with MERLIN.

> **Note:** Docker Compose maps the orchestrator to port 3838. When running locally via `python run.py` (outside Docker), the dev server also defaults to port 3838.

---

## WSL2 Networking

If you run Docker inside WSL2 and the SimConnect bridge on the Windows host, `localhost` inside WSL2 does not reach the Windows host. You must set `SIMCONNECT_WS_HOST` to the Windows host IP.

Find your Windows host IP from WSL:

```bash
powershell.exe -c "ipconfig" | grep "IPv4"
```

Or from Windows:

```powershell
(Get-NetIPAddress -InterfaceAlias "vEthernet (WSL*)" -AddressFamily IPv4).IPAddress
```

Update `.env`:

```bash
SIMCONNECT_WS_HOST=172.x.x.x    # your Windows host IP
```

The Docker Compose file already configures the orchestrator container to use `host.docker.internal` for the bridge URL, which resolves correctly when using Docker Desktop. This setting is only needed if you run the web server outside Docker.

---

## Troubleshooting

### SimConnect COM Errors

**Symptom:** `COMException` or `Could not connect to SimConnect` when starting the bridge.

- Ensure MSFS 2024 is running before starting the bridge.
- Run (or re-run) the SimConnect MSI installer: `C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.msi`. This is required after MSFS updates that reset COM registration.
- Verify Developer Mode is enabled in MSFS (Options > General > Developers).
- Confirm the DLL path in `SimConnectBridge.csproj` is correct.
- The bridge must run as a native Windows process, not inside WSL or Docker.

### Whisper Model Download Time

The default `small` model (~500 MB) downloads on first container start. Larger models (`medium`, `large-v3`) can exceed 3 GB.

- Check download progress: `docker compose logs -f whisper`
- The model is cached in the `whisper_cache` Docker volume -- subsequent starts are fast.
- For faster development iteration, set `WHISPER_MODEL=tiny` in `.env` (~75 MB).
- If behind a corporate proxy, configure Docker Desktop proxy settings under Settings > Resources > Proxies.

### ChromaDB v2 API

Recent versions of ChromaDB have migrated to a v2 API. If you see errors related to API version mismatches:

- Ensure you are using `chromadb/chroma:latest` in `docker-compose.yml` (this is the default).
- If the orchestrator reports collection errors, clear the persistent data and re-ingest:
  ```bash
  rm -rf data/chroma_db
  docker compose restart chromadb
  ```
- The orchestrator uses the ChromaDB HTTP client. Verify the endpoint: `curl http://localhost:8000/api/v1/heartbeat`

### Bridge Connects but No Telemetry

- You must be in an active flight (free flight, bush trip, etc.). The main menu does not expose SimConnect data.
- Check bridge console output for connection status messages.

### Docker Networking Issues

**Symptom:** Orchestrator cannot reach the SimConnect bridge.

- Inside Docker, the bridge is reached via `host.docker.internal`, not `localhost`. The `docker-compose.yml` already configures this.
- If running the web server natively (outside Docker), use `ws://localhost:8080` in `.env`.
- Verify ports are not in use: `netstat -ano | findstr :8080`
- Restart Docker Desktop if port mappings fail after system sleep/wake.

### Audio Device Issues

**Symptom:** No microphone input in the browser.

- Grant microphone permission when the browser prompts.
- Use HTTPS or `localhost` -- browsers block microphone access on plain HTTP from non-localhost origins.
- Check browser developer console for audio errors.

### Common Error Messages

| Error | Cause | Fix |
|---|---|---|
| `anthropic.AuthenticationError` | Invalid or missing API key | Check `ANTHROPIC_API_KEY` in `.env` |
| `ConnectionRefusedError` (ws://...:8080) | Bridge not running | Start: `cd simconnect-bridge && dotnet run` |
| `httpx.ConnectError` to port 9090 | Whisper not ready | Run `docker compose up -d whisper`, wait for healthy |
| `FileNotFoundError: SimConnect.dll` | Wrong SDK path | Update HintPath in `SimConnectBridge.csproj` |
| `COMException` | SimConnect not registered | Re-run `SimConnect.msi` from SDK |
