"""Voice pipeline: microphone input with Whisper STT and ElevenLabs TTS.

Includes audio preprocessing, aviation-vocabulary-biased transcription,
confidence scoring, and cancellable TTS playback for barge-in support.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from enum import Enum

import httpx
import numpy as np

from .audio_processing import (
    AVIATION_PROMPT,
    preprocess_audio,
    samples_to_wav_bytes,
)

logger = logging.getLogger(__name__)


class InputMode(str, Enum):
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
        vad_silence_duration: float = 1.5,
        mode: InputMode = InputMode.PUSH_TO_TALK,
    ) -> None:
        self._whisper_url = whisper_url.rstrip("/")
        self._sample_rate = sample_rate
        self._channels = channels
        self._vad_threshold = vad_threshold
        self._vad_silence_secs = vad_silence_duration
        self._mode = mode
        self._recording = False

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
        """Record audio using voice activity detection."""
        import sounddevice as sd

        logger.debug("VAD recording started")
        frames: list[np.ndarray] = []
        silence_frames = 0
        speech_detected = False
        silence_limit = int(self._vad_silence_secs * self._sample_rate / 1024)

        event = asyncio.Event()
        result_audio: list[np.ndarray | None] = [None]

        def callback(
            indata: np.ndarray, frame_count: int, time_info: dict, status: int
        ) -> None:
            nonlocal silence_frames, speech_detected
            rms = np.sqrt(np.mean(indata**2))
            frames.append(indata.copy())

            if rms > self._vad_threshold:
                speech_detected = True
                silence_frames = 0
            elif speech_detected:
                silence_frames += 1
                if silence_frames >= silence_limit:
                    result_audio[0] = np.concatenate(frames, axis=0).flatten()
                    event.set()

        stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            blocksize=1024,
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
                        "initial_prompt": AVIATION_PROMPT,
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

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        return samples_to_wav_bytes(audio, self._sample_rate, self._channels)

    async def listen(self) -> str:
        """Record based on current mode and return transcription."""
        if self._mode == InputMode.PUSH_TO_TALK:
            audio = await self.record_ptt()
        else:
            audio = await self.record_vad()
        return await self.transcribe(audio)


class VoiceOutput:
    """ElevenLabs TTS with streaming playback via ffmpeg for MP3 decoding.

    Supports cancellation for barge-in: call cancel() to stop the current
    playback immediately when the user starts speaking.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_multilingual_v2",
        sample_rate: int = 24000,
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._sample_rate = sample_rate
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

    def reset(self) -> None:
        """Reset cancellation state for a new response."""
        self._cancelled = False

    async def speak(self, text: str) -> None:
        """Convert text to speech and play through default audio output."""
        if not self._api_key or not self._voice_id:
            logger.warning("TTS not configured (missing api_key or voice_id)")
            return

        if not text.strip():
            return

        self.reset()
        mp3_data = await self._synthesize(text)
        if mp3_data and not self._cancelled:
            await self._play_mp3(mp3_data)

    async def speak_streamed(self, text_stream: AsyncIterator[str]) -> None:
        """Buffer text into sentences, synthesize each, and play sequentially.

        Respects cancellation: stops synthesizing and playing if cancel() is called.
        """
        if not self._api_key or not self._voice_id:
            logger.warning("TTS not configured, skipping speech output")
            return

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
                    mp3_data = await self._synthesize(sentence)
                    if mp3_data and not self._cancelled:
                        await self._play_mp3(mp3_data)

        # Flush remaining
        if buffer.strip() and not self._cancelled:
            mp3_data = await self._synthesize(buffer.strip())
            if mp3_data and not self._cancelled:
                await self._play_mp3(mp3_data)

    async def _synthesize(self, text: str) -> bytes | None:
        """Call ElevenLabs API and return MP3 audio bytes."""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.3,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                logger.info(
                    "TTS synthesized %d bytes for: %s", len(resp.content), text[:60]
                )
                return resp.content
            except httpx.HTTPError as e:
                logger.warning("TTS synthesis failed: %s", e)
                return None

    async def _play_mp3(self, mp3_data: bytes) -> None:
        """Decode MP3 via ffmpeg subprocess and play as PCM through sounddevice."""
        loop = asyncio.get_event_loop()
        try:
            pcm_data = await self._decode_mp3(mp3_data)
            if pcm_data is not None and not self._cancelled:
                self._playing = True
                await loop.run_in_executor(None, self._play_pcm, pcm_data)
                self._playing = False
        except Exception:
            self._playing = False
            logger.exception("Audio playback failed")

    async def _decode_mp3(self, mp3_data: bytes) -> np.ndarray | None:
        """Decode MP3 to PCM float32 array using ffmpeg."""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(self._sample_rate),
            "-ac",
            "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=mp3_data)

        if proc.returncode != 0:
            logger.warning("ffmpeg decode failed: %s", stderr.decode()[:200])
            return None

        if len(stdout) == 0:
            return None

        samples = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    def _play_pcm(self, samples: np.ndarray) -> None:
        """Synchronous PCM playback via sounddevice."""
        import sounddevice as sd

        sd.play(samples, samplerate=self._sample_rate)
        sd.wait()
