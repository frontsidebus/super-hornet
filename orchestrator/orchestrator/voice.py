"""Voice pipeline: microphone input with Whisper STT and TTS output.

Includes audio preprocessing, aviation-vocabulary-biased transcription,
confidence scoring, and cancellable TTS playback for barge-in support.
TTS synthesis is delegated to a pluggable TTSProvider backend.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from enum import StrEnum

import numpy as np

from .tts.base import TTSProvider

from .audio_processing import (
    SC_VOCABULARY_PROMPT,
    SileroVAD,
    preprocess_audio,
    samples_to_wav_bytes,
)

logger = logging.getLogger(__name__)


class InputMode(StrEnum):
    PUSH_TO_TALK = "push_to_talk"
    VOICE_ACTIVITY = "voice_activity"


class VoiceInput:
    """Handles microphone recording, VAD, and transcription via Docker Whisper service."""

    def __init__(
        self,
        whisper_url: str = "http://localhost:9090",
        sample_rate: int = 16000,
        channels: int = 1,
        vad_threshold: float = 0.02,
        vad_silence_duration: float = 0.4,
        mode: InputMode = InputMode.PUSH_TO_TALK,
    ) -> None:
        self._whisper_url = whisper_url.rstrip("/")
        self._sample_rate = sample_rate
        self._channels = channels
        self._vad_threshold = vad_threshold
        self._vad_silence_secs = vad_silence_duration
        self._mode = mode
        self._recording = False
        self._vad = SileroVAD(threshold=0.5, silence_ms=400)

    @property
    def mode(self) -> InputMode:
        return self._mode

    @mode.setter
    def mode(self, value: InputMode) -> None:
        self._mode = value

    async def record_ptt(self) -> np.ndarray:
        """Record audio while push-to-talk is active."""
        import sounddevice as sd

        logger.debug("PTT recording started")
        frames: list[np.ndarray] = []
        self._recording = True

        def callback(
            indata: np.ndarray, frame_count: int, time_info: dict, status: int
        ) -> None:
            if self._recording:
                frames.append(indata.copy())

        stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            callback=callback,
        )
        stream.start()

        while self._recording:
            await asyncio.sleep(0.05)

        stream.stop()
        stream.close()

        if not frames:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames, axis=0).flatten()

    def stop_recording(self) -> None:
        self._recording = False

    async def record_vad(self) -> np.ndarray:
        """Record audio using voice activity detection.

        Uses Silero VAD for neural speech endpoint detection when available.
        Falls back to RMS-based detection if torch is not installed.
        """
        import sounddevice as sd

        use_silero = self._vad.available
        if use_silero:
            logger.debug("VAD recording started (Silero neural VAD)")
            self._vad.reset()
        else:
            logger.debug("VAD recording started (RMS fallback)")

        frames: list[np.ndarray] = []
        silence_frames = 0
        speech_detected = False
        blocksize = 1024
        chunk_duration_ms = int(blocksize / self._sample_rate * 1000)
        # RMS fallback uses the configured silence duration
        rms_silence_limit = int(self._vad_silence_secs * self._sample_rate / blocksize)

        event = asyncio.Event()
        result_audio: list[np.ndarray | None] = [None]

        def callback(
            indata: np.ndarray, frame_count: int, time_info: dict, status: int
        ) -> None:
            nonlocal silence_frames, speech_detected
            chunk = indata.copy()
            frames.append(chunk)
            flat = chunk.flatten()

            if use_silero:
                prob = self._vad.speech_probability(flat, self._sample_rate)
                is_speech = prob >= self._vad._threshold

                if is_speech:
                    speech_detected = True
                    silence_frames = 0
                elif speech_detected:
                    silence_frames += 1
                    accumulated_ms = silence_frames * chunk_duration_ms
                    if accumulated_ms >= self._vad._silence_ms:
                        result_audio[0] = np.concatenate(
                            frames, axis=0
                        ).flatten()
                        event.set()
            else:
                # RMS fallback
                rms = np.sqrt(np.mean(flat**2))
                if rms > self._vad_threshold:
                    speech_detected = True
                    silence_frames = 0
                elif speech_detected:
                    silence_frames += 1
                    if silence_frames >= rms_silence_limit:
                        result_audio[0] = np.concatenate(
                            frames, axis=0
                        ).flatten()
                        event.set()

        stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            blocksize=blocksize,
            callback=callback,
        )
        stream.start()
        await event.wait()
        stream.stop()
        stream.close()

        return (
            result_audio[0]
            if result_audio[0] is not None
            else np.array([], dtype=np.float32)
        )

    async def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio via the local Docker Whisper HTTP service.

        Applies audio preprocessing (high-pass filter, silence trimming,
        normalization) and sends an aviation-vocabulary prompt to bias
        recognition toward flight-related terms.
        """
        if audio.size == 0:
            return ""

        # Preprocess: filter noise, trim silence, normalize
        audio = preprocess_audio(audio, self._sample_rate)
        if audio.size == 0:
            logger.debug("Audio too short after preprocessing, skipping transcription")
            return ""

        wav_bytes = samples_to_wav_bytes(audio, self._sample_rate, self._channels)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    f"{self._whisper_url}/asr",
                    params={
                        "encode": "true",
                        "task": "transcribe",
                        "language": "en",
                        "output": "json",
                        "initial_prompt": SC_VOCABULARY_PROMPT,
                    },
                    files={"audio_file": ("audio.wav", wav_bytes, "audio/wav")},
                )
                resp.raise_for_status()
                text = resp.json().get("text", "").strip()
                logger.info("Transcribed: %s", text)
                return text
            except httpx.HTTPError as e:
                logger.warning("Whisper transcription failed: %s", e)
                return ""

    async def listen(self) -> str:
        """Record based on current mode and return transcription."""
        if self._mode == InputMode.PUSH_TO_TALK:
            audio = await self.record_ptt()
        else:
            audio = await self.record_vad()
        return await self.transcribe(audio)


class VoiceOutput:
    """TTS playback via pluggable TTSProvider with barge-in support.

    Receives PCM int16 audio from the provider, converts to float32,
    and plays through sounddevice. Supports cancellation for barge-in.
    """

    def __init__(self, provider: TTSProvider) -> None:
        self._provider = provider
        self._sample_rate = provider.sample_rate
        self._cancelled = False
        self._playing = False

    @property
    def is_playing(self) -> bool:
        """Whether TTS audio is currently being played."""
        return self._playing

    def cancel(self) -> None:
        """Cancel current TTS playback for barge-in support."""
        self._cancelled = True
        if self._playing:
            try:
                import sounddevice as sd

                sd.stop()
            except Exception:
                pass
            self._playing = False
            logger.info("TTS playback cancelled (barge-in)")
        asyncio.ensure_future(self._provider.cancel())

    def reset(self) -> None:
        """Reset cancellation state for a new response."""
        self._cancelled = False

    async def speak(self, text: str) -> None:
        """Convert text to speech and play through default audio output."""
        if not text.strip():
            return

        self.reset()
        pcm_bytes = await self._provider.synthesize(text)
        if pcm_bytes and not self._cancelled:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            await self._play_pcm_async(samples)

    async def speak_streamed(self, text_stream: AsyncIterator[str]) -> None:
        """Buffer text into sentences, synthesize each via streaming, and play.

        Respects cancellation: stops synthesizing and playing if cancel() is called.
        """
        self.reset()
        buffer = ""
        sentence_endings = ".!?\n"

        async for chunk in text_stream:
            if self._cancelled:
                break

            buffer += chunk

            # Find the last sentence boundary
            last_boundary = -1
            for i, ch in enumerate(buffer):
                if ch in sentence_endings:
                    last_boundary = i

            if last_boundary >= 0:
                sentence = buffer[: last_boundary + 1].strip()
                buffer = buffer[last_boundary + 1 :]
                if sentence and not self._cancelled:
                    async for pcm_chunk in self._provider.synthesize_stream(sentence):
                        if self._cancelled:
                            break
                        if pcm_chunk:
                            samples = (
                                np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32)
                                / 32768.0
                            )
                            await self._play_pcm_async(samples)

        # Flush remaining
        if buffer.strip() and not self._cancelled:
            async for pcm_chunk in self._provider.synthesize_stream(buffer.strip()):
                if self._cancelled:
                    break
                if pcm_chunk:
                    samples = (
                        np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    )
                    await self._play_pcm_async(samples)

    async def _play_pcm_async(self, samples: np.ndarray) -> None:
        """Play PCM float32 samples via sounddevice in executor."""
        loop = asyncio.get_running_loop()
        try:
            self._playing = True
            await loop.run_in_executor(None, self._play_pcm, samples)
            self._playing = False
        except Exception:
            self._playing = False
            logger.exception("Audio playback failed")

    def _play_pcm(self, samples: np.ndarray) -> None:
        """Synchronous PCM playback via sounddevice."""
        import sounddevice as sd

        sd.play(samples, samplerate=self._sample_rate)
        sd.wait()
