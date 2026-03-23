"""Star Citizen text preprocessor for TTS output.

Converts LLM output into clean, speakable text for Star Citizen contexts.
Handles SC-specific acronyms, currency (aUEC/UEC), distances, shield/fuel
percentages, and general markdown cleanup.

Usage:
    from orchestrator.tts_preprocessor import preprocess_for_tts

    clean = preprocess_for_tts("Shield at 45%, 2300 aUEC bounty, target 3.5km out")
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Digit-to-word mappings
# ---------------------------------------------------------------------------

_DIGIT_WORDS_PLAIN: dict[str, str] = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}

# Standard English number words for natural readback.
_ONES = [
    "", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety",
]

_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# ---------------------------------------------------------------------------
# Star Citizen acronyms that TTS engines mangle if not expanded.
# ---------------------------------------------------------------------------

_SC_ACRONYMS: dict[str, str] = {
    "SCM": "S C M",
    "QT": "quantum travel",
    "aUEC": "alpha U E C",
    "UEC": "U E C",
    "SCU": "S C U",
    "EMP": "E M P",
    "HUD": "H U D",
    "EVA": "E V A",
    "NPC": "N P C",
    "PVP": "P V P",
    "PVE": "P V E",
    "DPS": "D P S",
    "IR": "I R",
    "EM": "E M",
    "CS": "crime stat",
    "QD": "quantum drive",
    "MFD": "M F D",
}


# ---------------------------------------------------------------------------
# Number helpers
# ---------------------------------------------------------------------------


def _digits_to_words(digits: str) -> str:
    """Convert a string of digits to individual spoken words."""
    return " ".join(_DIGIT_WORDS_PLAIN[d] for d in digits if d in _DIGIT_WORDS_PLAIN)


def _number_to_words(n: int) -> str:
    """Convert an integer (0-99999) to spoken English words.

    Used for currency amounts, distances, and percentages.
    """
    if n < 0:
        return "minus " + _number_to_words(-n)
    if n == 0:
        return "zero"

    parts: list[str] = []
    remaining = n

    if remaining >= 1_000_000:
        millions = remaining // 1_000_000
        parts.append(_number_to_words(millions))
        parts.append("million")
        remaining %= 1_000_000

    if remaining >= 1000:
        thousands = remaining // 1000
        if thousands >= 100:
            parts.append(_ONES[thousands // 100])
            parts.append("hundred")
            thousands %= 100
        if thousands >= 20:
            parts.append(_TENS[thousands // 10])
            if thousands % 10:
                parts.append(_ONES[thousands % 10])
        elif thousands >= 1:
            parts.append(_ONES[thousands])
        parts.append("thousand")
        remaining %= 1000

    if remaining >= 100:
        parts.append(_ONES[remaining // 100])
        parts.append("hundred")
        remaining %= 100

    if remaining >= 20:
        parts.append(_TENS[remaining // 10])
        remaining %= 10

    if remaining > 0:
        parts.append(_ONES[remaining])

    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Star Citizen-specific transformers
# ---------------------------------------------------------------------------


def _expand_sc_acronyms(text: str) -> str:
    """Expand Star Citizen acronyms that TTS engines mispronounce.

    Only expands standalone acronyms (word boundaries) to avoid
    mangling words that happen to contain the same letters.
    Processes longer acronyms first to avoid partial matches
    (e.g. 'aUEC' before 'UEC').
    """
    # Sort by length descending so 'aUEC' is matched before 'UEC'
    for acronym, spoken in sorted(
        _SC_ACRONYMS.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if acronym != spoken:
            text = re.sub(rf"\b{re.escape(acronym)}\b", spoken, text)
    return text


def _expand_sc_currency(text: str) -> str:
    """Expand aUEC/UEC currency amounts into natural speech.

    Amounts under 1000 are read digit-by-digit for clarity (e.g. "250 aUEC"
    → "two five zero alpha U E C"). Amounts 1000+ are read naturally
    (e.g. "15000 aUEC" → "fifteen thousand alpha U E C").
    """

    def _repl(m: re.Match[str]) -> str:
        raw = m.group(1).replace(",", "")
        n = int(raw)
        currency = m.group(2)
        spoken_currency = "alpha U E C" if currency.lower() == "auec" else "U E C"

        if n < 1000:
            spoken_amount = _digits_to_words(raw.lstrip("0") or "0")
        else:
            spoken_amount = _number_to_words(n)

        return spoken_amount + " " + spoken_currency

    return re.sub(
        r"\b(\d{1,3}(?:,?\d{3})*)\s*(aUEC|UEC)\b",
        _repl,
        text,
        flags=re.IGNORECASE,
    )


def _expand_sc_distances(text: str) -> str:
    """Expand km/m distances into natural speech.

    Examples:
        3.5km → three point five kilometers
        800m → eight hundred meters
        12km → twelve kilometers
    """

    def _repl_km(m: re.Match[str]) -> str:
        integer_part = m.group(1).replace(",", "")
        decimal_part = m.group(2)
        n = int(integer_part)
        unit = "kilometer" if n == 1 and not decimal_part else "kilometers"

        if decimal_part:
            spoken = _number_to_words(n) + " point " + _digits_to_words(decimal_part)
        else:
            spoken = _number_to_words(n)

        return spoken + " " + unit

    text = re.sub(
        r"\b(\d{1,6}(?:,\d{3})*)(?:\.(\d+))?\s*km\b",
        _repl_km,
        text,
        flags=re.IGNORECASE,
    )

    def _repl_m(m: re.Match[str]) -> str:
        raw = m.group(1).replace(",", "")
        n = int(raw)
        unit = "meter" if n == 1 else "meters"
        return _number_to_words(n) + " " + unit

    text = re.sub(
        r"\b(\d{1,6}(?:,\d{3})*)\s*m\b",
        _repl_m,
        text,
    )

    return text


def _expand_sc_percentages(text: str) -> str:
    """Expand shield/fuel/hull percentages into natural speech.

    Examples:
        45% → forty five percent
        100% shields → one hundred percent shields
        0% → zero percent
    """

    def _repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        suffix = m.group(2) or ""
        spoken = _number_to_words(n) + " percent"
        if suffix:
            spoken += " " + suffix.strip()
        return spoken

    return re.sub(
        r"\b(\d{1,3})%(\s+(?:shield|shields|hull|fuel|hydrogen|quantum|power|health))?",
        _repl,
        text,
        flags=re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Markdown and general cleanup
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting, preserving the underlying text."""
    # Code blocks (``` ... ```) → just the content
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)

    # Inline code `text` → just text
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Markdown links [text](url) → just the link text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Bold+italic ***text*** or ___text___
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text)

    # Bold **text** or __text__
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text)

    # Italic *text* or _text_ (not mid-word underscores like pre_flight)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)

    # Strikethrough ~~text~~
    text = re.sub(r"~~(.+?)~~", r"\1", text)

    # Headings: ### text → text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)

    # Blockquotes: > text → text
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    # Horizontal rules (---, ***, ___) → pause
    text = re.sub(r"^[-*_]{3,}\s*$", ".", text, flags=re.MULTILINE)

    # Bullet points (-, *, bullet) at line start → natural pause
    text = re.sub(r"^\s*[-*\u2022]\s+", ". ", text, flags=re.MULTILINE)

    # Numbered lists: 1. or 1) → natural pause
    text = re.sub(r"^\s*\d+[.)]\s+", ". ", text, flags=re.MULTILINE)

    # Any remaining stray asterisks
    text = text.replace("*", "")

    return text


def _replace_special_chars(text: str) -> str:
    """Convert special characters to speakable equivalents."""
    text = text.replace("\u2014", ", ")      # em dash
    text = text.replace("\u2013", " to ")    # en dash
    text = text.replace("\u2026", "...")      # ellipsis
    text = text.replace("\u00b0", " degrees")  # degree sign
    text = text.replace("\u00b1", " plus or minus ")  # plus-minus
    text = text.replace("&", " and ")
    text = text.replace("|", ", ")
    text = text.replace("~", "approximately ")

    # Slash: preserve in numeric contexts like 3/4, expand otherwise
    text = re.sub(r"(\d)\s*/\s*(\d)", r"\1 slash \2", text)
    text = re.sub(r"(?<!\d)/(?!\d)", " ", text)

    return text


def _clean_whitespace(text: str) -> str:
    """Collapse excess whitespace and normalize sentence breaks."""
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = re.sub(r"\n+", ". ", text)
    text = re.sub(r"[.,]{2,}", ".", text)
    text = re.sub(r"\.\s*,", ".", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def preprocess_for_tts(text: str) -> str:
    """Convert LLM output into clean, speakable text for TTS engines.

    Applies Star Citizen-specific transformations (currency, distances,
    percentages, acronyms) followed by markdown stripping and whitespace
    cleanup.

    Args:
        text: Raw text from the LLM, possibly containing markdown and
              Star Citizen terminology.

    Returns:
        Plain speakable text suitable for ElevenLabs or similar TTS.
    """
    if not text:
        return ""

    # --- Star Citizen transformations (order matters) ---
    # Currency before acronyms (so "aUEC" in "2300 aUEC" is handled as currency)
    text = _expand_sc_currency(text)
    # Distances before general cleanup
    text = _expand_sc_distances(text)
    # Percentages (shield, fuel, hull, etc.)
    text = _expand_sc_percentages(text)
    # SC acronyms (after specific patterns to avoid interfering)
    text = _expand_sc_acronyms(text)

    # --- General cleanup ---
    text = _strip_markdown(text)
    text = _replace_special_chars(text)
    text = _clean_whitespace(text)

    return text
