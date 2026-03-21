"""Client for the local Whisper speech-to-text HTTP service.

Communicates with a Whisper ASR server (onerahmet/openai-whisper-asr-webservice)
to transcribe audio without sending data to any external API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from .audio_processing import AVIATION_PROMPT

logger = logging.getLogger(__name__)

OutputFormat = Literal["text", "json", "verbose_json", "srt", "vtt"]

# Defaults
_DEFAULT_WHISPER_URL = "http://localhost:9000"
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # seconds, multiplied by attempt number

# Confidence threshold below which a transcription is considered unreliable
_LOW_CONFIDENCE_THRESHOLD = 0.4


@dataclass
class TranscriptionResult:
    """Result of a Whisper transcription with metadata."""

    text: str
    confidence: float  # 0.0 to 1.0, averaged across segments
    language: str
    duration_secs: float


class WhisperClientError(Exception):
    """Raised when the Whisper service returns an error or is unreachable."""


class WhisperClient:
    """HTTP client for a local Whisper ASR service.

    Args:
        base_url: Root URL of the Whisper service (e.g. http://whisper:9000).
        timeout: Request timeout in seconds.
        language: Optional language hint (ISO 639-1 code, e.g. "en").
        initial_prompt: Optional prompt to bias recognition toward specific vocabulary.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_WHISPER_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        language: str | None = "en",
        initial_prompt: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.language = language
        self.initial_prompt = initial_prompt or AVIATION_PROMPT
        self._client = httpx.Client(timeout=self.timeout)

    # -- Public API -----------------------------------------------------------

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        output_format: OutputFormat = "text",
        language: str | None = None,
    ) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio data (WAV, MP3, FLAC, etc.).
            output_format: Desired response format from the ASR server.
            language: Override the default language for this request.

        Returns:
            The transcribed text.

        Raises:
            WhisperClientError: If transcription fails after all retries.
        """
        lang = language or self.language
        params: dict[str, str] = {
            "output": output_format,
            "task": "transcribe",
            "encode": "true",
        }
        if lang:
            params["language"] = lang
        if self.initial_prompt:
            params["initial_prompt"] = self.initial_prompt

        url = f"{self.base_url}/asr"
        files = {"audio_file": ("audio.wav", audio_bytes, "audio/wav")}

        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.post(url, params=params, files=files)
                response.raise_for_status()
                result = response.text.strip()
                logger.debug("Whisper transcription (%d chars): %s...", len(result), result[:80])
                return result
            except httpx.ConnectError as exc:
                last_error = exc
                wait = _RETRY_BACKOFF * attempt
                logger.warning(
                    "Whisper service unreachable (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                    exc,
                )
                time.sleep(wait)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.error(
                    "Whisper returned HTTP %d: %s",
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                # Don't retry on client errors (4xx) -- the request itself is bad
                if 400 <= exc.response.status_code < 500:
                    break
                wait = _RETRY_BACKOFF * attempt
                time.sleep(wait)
            except httpx.TimeoutException as exc:
                last_error = exc
                wait = _RETRY_BACKOFF * attempt
                logger.warning(
                    "Whisper request timed out (attempt %d/%d), retrying in %.1fs",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)

        msg = f"Whisper transcription failed after {_MAX_RETRIES} attempts"
        raise WhisperClientError(msg) from last_error

    def transcribe_with_confidence(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe audio and return result with confidence scoring.

        Uses verbose_json output to extract per-segment confidence (avg_logprob).
        Falls back to a default confidence of 0.5 if metadata is unavailable.

        Args:
            audio_bytes: Raw audio data (WAV, MP3, FLAC, etc.).
            language: Override the default language for this request.

        Returns:
            TranscriptionResult with text, confidence, language, and duration.

        Raises:
            WhisperClientError: If transcription fails after all retries.
        """
        lang = language or self.language
        params: dict[str, str] = {
            "output": "verbose_json",
            "task": "transcribe",
            "encode": "true",
        }
        if lang:
            params["language"] = lang
        if self.initial_prompt:
            params["initial_prompt"] = self.initial_prompt

        url = f"{self.base_url}/asr"
        files = {"audio_file": ("audio.wav", audio_bytes, "audio/wav")}

        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.post(url, params=params, files=files)
                response.raise_for_status()
                data = response.json()
                return self._parse_verbose_response(data)
            except httpx.ConnectError as exc:
                last_error = exc
                wait = _RETRY_BACKOFF * attempt
                logger.warning(
                    "Whisper service unreachable (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                    exc,
                )
                time.sleep(wait)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.error(
                    "Whisper returned HTTP %d: %s",
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                if 400 <= exc.response.status_code < 500:
                    break
                wait = _RETRY_BACKOFF * attempt
                time.sleep(wait)
            except httpx.TimeoutException as exc:
                last_error = exc
                wait = _RETRY_BACKOFF * attempt
                logger.warning(
                    "Whisper request timed out (attempt %d/%d), retrying in %.1fs",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)

        msg = f"Whisper transcription failed after {_MAX_RETRIES} attempts"
        raise WhisperClientError(msg) from last_error

    def _parse_verbose_response(self, data: dict) -> TranscriptionResult:
        """Extract text, confidence, language, and duration from verbose_json response."""
        text = data.get("text", "").strip()
        detected_lang = data.get("language", self.language or "en")
        duration = data.get("duration", 0.0)

        # Calculate average confidence from segment avg_logprob values.
        # avg_logprob is negative; closer to 0 = higher confidence.
        # We map it to 0..1 using: confidence = exp(avg_logprob).
        segments = data.get("segments", [])
        if segments:
            import math

            logprobs = [s.get("avg_logprob", -1.0) for s in segments]
            avg_logprob = sum(logprobs) / len(logprobs)
            # exp(-1.0) ~ 0.37, exp(-0.2) ~ 0.82, exp(0) = 1.0
            confidence = min(1.0, max(0.0, math.exp(avg_logprob)))
        else:
            confidence = 0.5  # unknown

        logger.info(
            "Whisper: '%s' (confidence=%.2f, lang=%s, dur=%.1fs)",
            text[:80],
            confidence,
            detected_lang,
            duration,
        )

        return TranscriptionResult(
            text=text,
            confidence=confidence,
            language=detected_lang,
            duration_secs=duration,
        )

    def is_available(self) -> bool:
        """Check whether the Whisper service is reachable."""
        try:
            resp = self._client.get(f"{self.base_url}/docs", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> WhisperClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
