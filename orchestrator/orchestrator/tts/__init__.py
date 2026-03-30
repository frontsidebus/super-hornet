"""TTS provider package with factory function and base class re-exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TTSProvider

if TYPE_CHECKING:
    from orchestrator.config import Settings


def create_tts_provider(settings: Settings) -> TTSProvider:
    """Create and return the TTS provider specified in settings.

    Dispatches on ``settings.tts_provider`` (case-insensitive, whitespace-stripped).
    Provider modules are imported lazily so optional dependencies
    (e.g. kokoro-onnx) are only required when actually selected.
    """
    provider = settings.tts_provider.lower().strip()
    if provider == "elevenlabs":
        from .elevenlabs import ElevenLabsTTS

        return ElevenLabsTTS(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.elevenlabs_voice_id,
            model_id=settings.elevenlabs_model_id,
        )
    if provider == "kokoro":
        from .kokoro import KokoroTTS

        return KokoroTTS(
            model_path=settings.kokoro_model_path,
            voices_path=settings.kokoro_voices_path,
            voice=settings.kokoro_voice,
        )
    raise ValueError(
        f"Unknown TTS provider: {provider!r}. Use 'elevenlabs' or 'kokoro'."
    )


__all__ = ["TTSProvider", "create_tts_provider"]
