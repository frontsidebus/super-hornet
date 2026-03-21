"""Audio preprocessing utilities for improving Whisper transcription accuracy.

Provides noise reduction, silence trimming, normalization, and format conversion
optimized for cockpit audio environments.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave

import numpy as np

logger = logging.getLogger(__name__)

# Aviation-specific vocabulary used as an initial_prompt hint for Whisper.
# This biases the model toward recognizing these terms without restricting output.
AVIATION_PROMPT = (
    "ATIS, METAR, TAF, ILS, VOR, NDB, DME, GPS, RNAV, RNP, SID, STAR, "
    "squawk, altimeter, QNH, QFE, flight level, "
    "roger, wilco, affirmative, negative, mayday, pan-pan, "
    "clearance, taxi, takeoff, go-around, missed approach, "
    "heading, altitude, airspeed, vertical speed, "
    "flaps, gear, trim, throttle, mixture, prop, "
    "Cessna, Boeing, Airbus, Piper, Beechcraft, "
    "alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel, india, "
    "juliet, kilo, lima, mike, november, oscar, papa, quebec, romeo, "
    "sierra, tango, uniform, victor, whiskey, x-ray, yankee, zulu, "
    "knots, feet, nautical miles, "
    "VREF, V1, VR, V2, VNE, VNO, VS0, VS1, VFE, "
    "autopilot, LNAV, VNAV, localizer, glideslope, "
    "Captain, MERLIN"
)

# Minimum audio duration in seconds to bother transcribing
MIN_AUDIO_DURATION_SECS = 0.3

# Target sample rate for Whisper
TARGET_SAMPLE_RATE = 16000


def normalize_audio(samples: np.ndarray) -> np.ndarray:
    """Normalize audio to use full dynamic range without clipping.

    Applies peak normalization to 95% of max to leave headroom.
    """
    if samples.size == 0:
        return samples

    peak = np.max(np.abs(samples))
    if peak < 1e-6:
        return samples  # silence, don't amplify noise

    return samples * (0.95 / peak)


def trim_silence(
    samples: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
    threshold_db: float = -40.0,
    min_silence_ms: int = 200,
) -> np.ndarray:
    """Remove leading and trailing silence from audio.

    Args:
        samples: Audio samples as float32 array.
        sample_rate: Sample rate in Hz.
        threshold_db: Silence threshold in dB (relative to peak).
        min_silence_ms: Minimum silence duration in ms to consider as silence.

    Returns:
        Trimmed audio samples.
    """
    if samples.size == 0:
        return samples

    # Convert threshold from dB to linear amplitude
    threshold = 10 ** (threshold_db / 20.0)

    # Calculate frame energy using short windows
    frame_size = int(sample_rate * min_silence_ms / 1000)
    if frame_size == 0 or samples.size < frame_size:
        return samples

    # Find first frame above threshold
    start = 0
    for i in range(0, samples.size - frame_size, frame_size):
        rms = np.sqrt(np.mean(samples[i : i + frame_size] ** 2))
        if rms > threshold:
            # Back up slightly to include attack transient
            start = max(0, i - frame_size)
            break
    else:
        return samples  # all silence

    # Find last frame above threshold
    end = samples.size
    for i in range(samples.size - frame_size, start, -frame_size):
        rms = np.sqrt(np.mean(samples[i : i + frame_size] ** 2))
        if rms > threshold:
            # Include a small tail for natural decay
            end = min(samples.size, i + frame_size * 2)
            break

    return samples[start:end]


def apply_highpass_filter(
    samples: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
    cutoff_hz: float = 80.0,
) -> np.ndarray:
    """Apply a simple first-order high-pass filter to remove low-frequency rumble.

    Cockpit environments have significant low-frequency engine noise that
    degrades transcription accuracy.
    """
    if samples.size == 0:
        return samples

    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / sample_rate
    alpha = rc / (rc + dt)

    output = np.zeros_like(samples)
    output[0] = samples[0]
    for i in range(1, len(samples)):
        output[i] = alpha * (output[i - 1] + samples[i] - samples[i - 1])

    return output


def is_audio_too_short(
    samples: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
    min_duration: float = MIN_AUDIO_DURATION_SECS,
) -> bool:
    """Check if audio is too short to produce meaningful transcription."""
    if samples.size == 0:
        return True
    duration = samples.size / sample_rate
    return duration < min_duration


def preprocess_audio(
    samples: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    """Full preprocessing pipeline for voice audio before Whisper transcription.

    Steps:
    1. High-pass filter to remove engine rumble
    2. Trim leading/trailing silence
    3. Normalize amplitude

    Args:
        samples: Raw float32 audio samples.
        sample_rate: Sample rate in Hz.

    Returns:
        Preprocessed audio samples, or empty array if audio is too short.
    """
    if samples.size == 0:
        return samples

    # Step 1: Remove low-frequency noise
    samples = apply_highpass_filter(samples, sample_rate)

    # Step 2: Trim silence
    samples = trim_silence(samples, sample_rate)

    # Step 3: Check minimum duration after trimming
    if is_audio_too_short(samples, sample_rate):
        logger.debug(
            "Audio too short after preprocessing (%.2fs)",
            samples.size / sample_rate,
        )
        return np.array([], dtype=np.float32)

    # Step 4: Normalize
    samples = normalize_audio(samples)

    return samples


def samples_to_wav_bytes(
    samples: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
    channels: int = 1,
) -> bytes:
    """Convert float32 samples to WAV file bytes."""
    buf = io.BytesIO()
    int16_audio = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_audio.tobytes())
    buf.seek(0)
    return buf.read()


def wav_bytes_to_samples(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Parse WAV bytes into float32 samples and sample rate."""
    buf = io.BytesIO(wav_bytes)
    try:
        with wave.open(buf, "rb") as wf:
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return samples, sample_rate
    except Exception:
        logger.warning("Failed to parse WAV bytes, returning empty")
        return np.array([], dtype=np.float32), TARGET_SAMPLE_RATE


async def convert_webm_to_wav_normalized(webm_bytes: bytes) -> bytes:
    """Convert webm/ogg audio to normalized 16kHz mono WAV using ffmpeg + preprocessing.

    This replaces the naive ffmpeg-only conversion with additional audio
    preprocessing to improve Whisper accuracy.
    """
    # Use ffmpeg to convert to raw PCM first
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", "pipe:0",
        "-ar", str(TARGET_SAMPLE_RATE),
        "-ac", "1",
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=webm_bytes)

    if proc.returncode != 0:
        logger.error("ffmpeg conversion failed: %s", stderr.decode(errors="replace")[:300])
        # Fall back to raw bytes
        return webm_bytes

    if len(stdout) == 0:
        logger.warning("ffmpeg produced empty output")
        return webm_bytes

    # Convert raw PCM to float32 samples
    samples = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0

    # Apply preprocessing pipeline
    samples = preprocess_audio(samples, TARGET_SAMPLE_RATE)

    if samples.size == 0:
        logger.info("Audio too short after preprocessing, returning minimal WAV")
        return samples_to_wav_bytes(np.zeros(TARGET_SAMPLE_RATE // 10, dtype=np.float32))

    return samples_to_wav_bytes(samples, TARGET_SAMPLE_RATE)
