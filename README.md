# Super Hornet

AI agent platform for [Star Citizen](https://robertsspaceindustries.com/en/). Voice-interactive wingman with real-time game awareness via screen capture and log parsing, powered by Claude.

## What It Does

Super Hornet is your AI co-pilot in the verse. It can:

- **See your game** -- Captures and analyzes your HUD via Claude Vision (shields, fuel, radar, quantum drive status)
- **Track events** -- Parses `game.log` in real-time for kills, deaths, location changes, and combat events
- **Advise on trades** -- Queries UEX Corp API for live commodity prices and optimal trade routes
- **Talk to you** -- Voice interaction via push-to-talk or voice activity detection (Whisper STT + ElevenLabs TTS)
- **Learn and act** -- Builds a skill library of verified action sequences and can optionally execute them via input simulation

## Architecture

**Constellation** -- three decoupled layers:

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   PERCEPTION    │    │   REASONING      │    │   ACTION        │
│                 │    │                  │    │                 │
│ • game.log      │───▶│ • Claude API     │───▶│ • Voice output  │
│ • Screen capture│    │ • Tool use       │    │ • Input sim     │
│ • UEX Corp API  │    │ • Skill library  │    │ • Overlay UI    │
│ • SC Wiki API   │    │ • Knowledge base │    │                 │
│ • Voice input   │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your API keys (Anthropic, ElevenLabs) and SC game.log path

# 2. Start services
docker compose up -d

# 3. Install and run
cd orchestrator
pip install -e ".[dev]"
hornet
```

See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for detailed setup instructions.

## Requirements

- Python 3.11+
- Docker Desktop (for Whisper + ChromaDB)
- Star Citizen installed (for game.log access)
- Anthropic API key
- ElevenLabs API key (for voice output)

## EAC Compliance

Super Hornet is designed to be fully compatible with Easy Anti-Cheat:

- **No memory reading** -- all game state comes from screen capture and log parsing
- **No DLL injection** -- completely out-of-process
- **No game file modification** -- read-only access to game.log
- **Input simulation** -- uses OS-level DirectInput (same as VoiceAttack), disabled by default

## License

[MIT](LICENSE)

## Credits

Forked from [airdale/MERLIN](https://github.com/frontsidebus/airdale) by frontsidebus.
