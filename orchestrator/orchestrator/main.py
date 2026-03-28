"""Super Hornet orchestrator main entry point.

Connects the perception layer (game.log parser, vision module), intelligence
layer (Claude API, knowledge base, skill library), and action layer (voice,
input simulation) into a unified conversation loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import Any

import httpx

from .claude_client import ClaudeClient
from .config import Settings, load_settings
from .context_store import ContextStore
from .game_activity import GameActivityDetector
from .game_client import GameStateClient
from .game_state import GameActivity, GameState
from .health import ConnectionState, HealthMonitor
from .input_simulator import InputSimulator
from .log_parser import LogParserModule
from .screen_capture import CaptureManager
from .skill_library import SkillLibrary
from .uex_client import UEXClient
from .vision import VisionModule, load_roi_definitions
from .voice import InputMode, VoiceInput, VoiceOutput

logger = logging.getLogger(__name__)


class Orchestrator:
    """Top-level coordinator that wires all subsystems together.

    Three-layer Constellation architecture:
    - Perception: LogParserModule, VisionModule, UEXClient
    - Reasoning: ClaudeClient, ContextStore, SkillLibrary, GameActivityDetector
    - Action: InputSimulator, VoiceOutput, Web UI
    """

    def __init__(self, settings: Settings, text_only: bool = False) -> None:
        self._settings = settings
        self._text_only = text_only

        # --- Perception layer ---
        self._log_parser = LogParserModule(settings.sc_game_log_path) if settings.sc_game_log_path else None

        roi_definitions = []
        if settings.vision_roi_config_path:
            try:
                roi_definitions = load_roi_definitions(settings.vision_roi_config_path)
            except Exception:
                logger.warning("Failed to load ROI definitions from %s", settings.vision_roi_config_path)

        self._vision_module = VisionModule(
            capture_fps=settings.vision_fps,
            roi_definitions=roi_definitions,
            anthropic_api_key=settings.anthropic_api_key,
        ) if settings.vision_enabled else None

        self._capture_manager = CaptureManager(
            fps=settings.screen_capture_fps,
            enabled=settings.screen_capture_enabled,
        )

        self._game_client = GameStateClient(
            log_parser=self._log_parser,
            vision_module=self._vision_module,
        )

        self._uex_client = UEXClient(
            base_url=settings.uex_api_base_url,
            api_key=settings.uex_api_key,
        )

        # --- Intelligence layer ---
        self._context_store = ContextStore(
            settings.chromadb_url,
            collection_name=settings.knowledge_base_collection,
        )
        self._skill_library = SkillLibrary(
            settings.chromadb_url,
            collection_name=settings.skill_library_collection,
        )
        self._activity_detector = GameActivityDetector()

        # --- Action layer ---
        self._input_sim = InputSimulator(enabled=settings.input_simulation_enabled)
        self._voice_input = VoiceInput(
            whisper_url=settings.whisper_url,
            mode=InputMode.PUSH_TO_TALK,
        )
        self._voice_output = VoiceOutput(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.voice_id,
        )

        # --- Claude client (wired to all subsystems) ---
        self._claude = ClaudeClient(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
            game_client=self._game_client,
            context_store=self._context_store,
            skill_library=self._skill_library,
            uex_client=self._uex_client,
            input_simulator=self._input_sim,
            max_tokens=settings.claude_max_tokens,
            max_tokens_briefing=settings.claude_max_tokens_briefing,
            max_history=settings.claude_max_history,
        )

        self._running = False
        self._game_connected = False
        self._tts_enabled = bool(
            settings.elevenlabs_api_key and settings.voice_id
        )

        # Health monitoring
        self._health = HealthMonitor()
        self._health.register("game_log")
        self._health.register("vision")
        self._health.register("chromadb")
        self._health.register("whisper")
        self._health.register("claude_api")
        self._health.register("uex_api")

        # Whisper degradation tracking
        self._whisper_available = True

    async def start(self) -> None:
        """Initialize all subsystems and enter the main loop."""
        logger.info("Super Hornet orchestrator starting up")

        # Start game state client (non-fatal if unavailable)
        if not self._text_only:
            try:
                await self._game_client.connect()
                self._game_connected = True
                self._game_client.subscribe(self._on_state_update)
                self._health.update("game_log", True, "Connected")
            except Exception:
                logger.warning(
                    "Could not start game state client; "
                    "running in text-only mode without live game data",
                )
                self._health.update(
                    "game_log",
                    False,
                    "Connection failed; text-only mode",
                )
        else:
            logger.info("Text-only mode: skipping game state client")
            self._health.update("game_log", False, "Skipped (text-only mode)")

        # Vision health
        if self._vision_module:
            self._health.update("vision", True, "Enabled")
        else:
            self._health.update("vision", False, "Disabled")

        # ChromaDB health
        if self._context_store.available:
            self._health.update("chromadb", True, "Connected")
        else:
            self._health.update("chromadb", False, "Unavailable; RAG disabled")

        # Whisper health check
        await self._check_whisper_health()

        # Claude API assumed healthy until a call fails
        self._health.update("claude_api", True, "Ready")

        # UEX API health (best effort)
        self._health.update("uex_api", True, "Ready")

        await self._capture_manager.start()

        self._running = True
        logger.info("Super Hornet is ready.")

        mode_label = (
            "text-only" if not self._game_connected else "game-connected"
        )
        tts_label = "TTS enabled" if self._tts_enabled else "TTS disabled"

        print(
            f"\n=== Super Hornet AI Wingman ({mode_label}, {tts_label}) ==="
        )
        print(
            "Type your message, or use /voice to toggle voice input."
        )
        print(
            "Commands: /voice, /vad, /ptt, /capture, /tts, "
            "/clear, /status, /health, /quit\n"
        )

        await self._conversation_loop()

    async def stop(self) -> None:
        self._running = False
        await self._capture_manager.stop()
        if self._game_connected:
            await self._game_client.disconnect()
        logger.info("Super Hornet orchestrator shut down")

    # -------------------------------------------------------------------
    # Health checks
    # -------------------------------------------------------------------

    async def _check_whisper_health(self) -> None:
        """Probe Whisper endpoint and update health status."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._settings.whisper_url}/docs"
                )
                if resp.status_code == 200:
                    self._whisper_available = True
                    self._health.update("whisper", True, "Responding")
                else:
                    self._whisper_available = False
                    self._health.update(
                        "whisper",
                        False,
                        f"HTTP {resp.status_code}; voice input degraded",
                    )
        except Exception as exc:
            self._whisper_available = False
            self._health.update(
                "whisper",
                False,
                f"Unreachable ({exc}); voice input degraded",
            )

    def _update_game_health(self) -> None:
        """Refresh game client health."""
        cs = self._game_client.connection_state
        if cs == ConnectionState.CONNECTED:
            self._health.update("game_log", True, "Connected")
        elif cs == ConnectionState.RECONNECTING:
            self._health.update("game_log", False, "Reconnecting...")
        else:
            self._health.update("game_log", False, "Disconnected")

    def get_health_summary(self) -> dict[str, Any]:
        """Return health summary for all subsystems."""
        self._update_game_health()
        return self._health.summary()

    # -------------------------------------------------------------------
    # Conversation loop
    # -------------------------------------------------------------------

    async def _conversation_loop(self) -> None:
        """Main loop: gather input, build context, call Claude, output."""
        use_voice = False

        while self._running:
            try:
                self._update_game_health()

                # Get user input
                if use_voice:
                    if not self._whisper_available:
                        print(
                            "[Whisper unavailable -- "
                            "falling back to text input]"
                        )
                        use_voice = False
                        continue

                    print("[Listening...]")
                    try:
                        user_text = await self._voice_input.listen()
                    except Exception:
                        logger.warning(
                            "Voice input failed; switching to text mode"
                        )
                        self._whisper_available = False
                        self._health.update(
                            "whisper",
                            False,
                            "Listen failed; text-only fallback",
                        )
                        use_voice = False
                        print(
                            "[Voice input failed -- "
                            "switched to text mode]"
                        )
                        continue
                    if user_text:
                        print(f"You: {user_text}")
                    else:
                        continue
                else:
                    try:
                        user_text = (
                            await asyncio.get_running_loop().run_in_executor(
                                None, lambda: input("Commander> ")
                            )
                        )
                    except EOFError:
                        break

                user_text = user_text.strip()
                if not user_text:
                    continue

                # Handle commands
                if user_text.startswith("/"):
                    cmd_lower = user_text.lower().strip()
                    handled = await self._handle_command(cmd_lower)
                    if handled:
                        if cmd_lower == "/voice":
                            use_voice = not use_voice
                        continue

                # Get current game state and detect activity
                game_state = self._get_current_game_state()

                # Optionally grab screen capture for vision
                image_b64 = None
                if self._capture_manager.enabled:
                    image_b64 = (
                        await self._capture_manager.get_frame_base64()
                    )

                # Stream Claude response
                print("Super Hornet: ", end="", flush=True)
                full_response = ""

                try:
                    async for chunk in self._claude.chat(
                        user_text,
                        game_state=game_state,
                        image_base64=image_b64,
                    ):
                        print(chunk, end="", flush=True)
                        full_response += chunk
                    self._health.update("claude_api", True, "OK")
                except Exception as exc:
                    logger.exception("Claude API error")
                    self._health.update(
                        "claude_api", False, f"Error: {exc}"
                    )
                    print(
                        "\n[Claude API error -- "
                        "check logs for details.]"
                    )
                    continue

                print()  # newline after response

                # TTS output (non-blocking)
                if self._tts_enabled and full_response:
                    task = asyncio.create_task(
                        self._voice_output.speak(full_response)
                    )
                    task.add_done_callback(self._on_tts_done)

            except KeyboardInterrupt:
                print("\nUse /quit to exit.")
            except Exception:
                logger.exception("Error in conversation loop")
                print(
                    "\n[Super Hornet encountered an error. "
                    "Check logs for details.]"
                )

    @staticmethod
    def _on_tts_done(task: asyncio.Task[None]) -> None:
        """Log exceptions from fire-and-forget TTS tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("TTS playback task failed: %s", exc)

    def _get_current_game_state(self) -> GameState:
        """Return the latest game state with detected activity."""
        if self._game_connected:
            cs = self._game_client.connection_state
            if cs == ConnectionState.CONNECTED:
                state = self._game_client.state
                detected_activity = self._activity_detector.update(state)
                state.activity = detected_activity
                return state
            logger.debug(
                "Game client is %s; using last-known state", cs.value
            )
            return self._game_client.state
        return GameState()

    async def _handle_command(self, cmd: str) -> bool:
        """Process slash commands. Returns True if command was handled."""
        if cmd == "/quit":
            self._running = False
            print("Shutting down Super Hornet...")
            return True

        if cmd == "/voice":
            print("Voice input toggled. (Handled by caller.)")
            return True

        if cmd == "/vad":
            self._voice_input.mode = InputMode.VOICE_ACTIVITY
            print("Switched to voice-activity-detection mode.")
            return True

        if cmd == "/ptt":
            self._voice_input.mode = InputMode.PUSH_TO_TALK
            print("Switched to push-to-talk mode.")
            return True

        if cmd == "/tts":
            self._tts_enabled = not self._tts_enabled
            state_label = "enabled" if self._tts_enabled else "disabled"
            print(f"TTS {state_label}.")
            return True

        if cmd == "/capture":
            if self._capture_manager.enabled:
                await self._capture_manager.stop()
                self._capture_manager.enabled = False
                print("Screen capture disabled.")
            else:
                self._capture_manager.enabled = True
                await self._capture_manager.start()
                print("Screen capture enabled.")
            return True

        if cmd == "/clear":
            self._claude.clear_history()
            print("Conversation history cleared.")
            return True

        if cmd == "/health":
            summary = self.get_health_summary()
            print("\n--- Subsystem Health ---")
            for name, info in summary.items():
                status = "OK" if info["healthy"] else "DEGRADED"
                age = info["age_seconds"]
                msg = info["message"]
                age_str = (
                    f" (last seen {age}s ago)"
                    if age != float("inf")
                    else ""
                )
                print(f"  {name}: {status} -- {msg}{age_str}")
            print()
            return True

        if cmd == "/status":
            self._update_game_health()
            if self._game_connected:
                cs = self._game_client.connection_state
                if cs == ConnectionState.CONNECTED:
                    state = self._game_client.state
                    print(f"Game: {cs.value} | {state.state_summary()}")
                else:
                    print(f"Game: {cs.value}")
            else:
                print("Game: Not connected (text-only mode)")

            print(
                f"Knowledge base: "
                f"{'available' if self._context_store.available else 'unavailable'}"
            )
            print(f"Docs in store: {await self._context_store.document_count()}")
            print(
                f"Skill library: "
                f"{'available' if self._skill_library.available else 'unavailable'}"
            )
            print(f"Skills: {await self._skill_library.skill_count()}")
            print(
                f"TTS: {'enabled' if self._tts_enabled else 'disabled'}"
            )
            print(
                f"Screen capture: "
                f"{'on' if self._capture_manager.enabled else 'off'}"
            )
            print(
                f"Whisper: "
                f"{'available' if self._whisper_available else 'unavailable'}"
            )
            print(
                f"Input sim: "
                f"{'enabled' if self._input_sim.enabled else 'disabled'}"
            )
            return True

        print(f"Unknown command: {cmd}")
        return True

    async def _on_state_update(self, state: GameState) -> None:
        """Callback for game state updates from the perception layer."""
        detected_activity = self._activity_detector.update(state)
        state.activity = detected_activity


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Super Hornet AI Wingman for Star Citizen"
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        default=False,
        help="Skip game state client connection (text chat only)",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = _parse_args()
    settings = load_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    orchestrator = Orchestrator(settings, text_only=args.text_only)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(orchestrator.stop()),
            )
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        await orchestrator.start()
    finally:
        await orchestrator.stop()


def run() -> None:
    """Entry point for the hornet console script."""
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
