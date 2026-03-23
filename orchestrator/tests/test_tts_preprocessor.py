"""Tests for the Star Citizen TTS preprocessor."""

from __future__ import annotations

import pytest

from orchestrator.tts_preprocessor import preprocess_for_tts


# ---------------------------------------------------------------------------
# Star Citizen acronyms
# ---------------------------------------------------------------------------


class TestSCAcronyms:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("Switch to SCM mode", "Switch to S C M mode"),
            ("Engage QT to Hurston", "Engage quantum travel to Hurston"),
            ("You earned 500 aUEC", "You earned five zero zero alpha U E C"),
            ("Price is 200 UEC", "Price is two zero zero U E C"),
            ("Load 32 SCU of cargo", "Load 32 S C U of cargo"),
            ("Hit them with the EMP", "Hit them with the E M P"),
            ("Check your HUD", "Check your H U D"),
            ("Exit via EVA", "Exit via E V A"),
            ("An NPC vendor", "An N P C vendor"),
            ("This is a PVP zone", "This is a P V P zone"),
            ("Great for PVE missions", "Great for P V E missions"),
            ("High DPS loadout", "High D P S loadout"),
            ("Low IR signature", "Low I R signature"),
            ("Reduce your EM emissions", "Reduce your E M emissions"),
            ("You have CS level 3", "You have crime stat level 3"),
            ("Spool the QD", "Spool the quantum drive"),
            ("Check the MFD", "Check the M F D"),
        ],
    )
    def test_acronym_expansion(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Star Citizen currency (aUEC / UEC)
# ---------------------------------------------------------------------------


class TestSCCurrency:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("Bounty is 15000 aUEC", "Bounty is fifteen thousand alpha U E C"),
            ("Costs 250 UEC", "Costs two five zero U E C"),
            ("Earned 1,500,000 aUEC", "Earned one million five hundred thousand alpha U E C"),
            ("Price: 99 aUEC", "Price: nine nine alpha U E C"),
            ("Worth 0 UEC", "Worth zero U E C"),
        ],
    )
    def test_currency_expansion(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Star Citizen distances (km / m)
# ---------------------------------------------------------------------------


class TestSCDistances:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("Target 3.5km out", "Target three point five kilometers out"),
            ("Range 12km", "Range twelve kilometers"),
            ("Distance 800m", "Distance eight hundred meters"),
            ("Only 1km away", "Only one kilometer away"),
            ("At 1m distance", "At one meter distance"),
        ],
    )
    def test_distance_expansion(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Shield / fuel / hull percentages
# ---------------------------------------------------------------------------


class TestSCPercentages:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("Shields at 45%", "Shields at forty five percent"),
            ("Hull at 100% hull", "Hull at one hundred percent hull"),
            ("Fuel down to 10% hydrogen", "Fuel down to ten percent hydrogen"),
            ("0% shields remaining", "zero percent shields remaining"),
            ("Quantum fuel 75% quantum", "Quantum fuel seventy five percent quantum"),
            ("Power at 50% power", "Power at fifty percent power"),
        ],
    )
    def test_percentage_expansion(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Markdown stripping
# ---------------------------------------------------------------------------


class TestMarkdown:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("**bold text**", "bold text"),
            ("*italic text*", "italic text"),
            ("~~struck out~~", "struck out"),
            ("### Heading Three", "Heading Three"),
            ("`inline code`", "inline code"),
            ("[link text](http://example.com)", "link text"),
        ],
    )
    def test_markdown_stripping(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Special characters
# ---------------------------------------------------------------------------


class TestSpecialChars:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("5\u00b0 nose up", "5 degrees nose up"),
            ("A \u2014 pause", "A , pause"),
            ("3 \u2013 5", "3 to 5"),
            ("~200m", "approximately two hundred meters"),
            ("A & B", "A and B"),
        ],
    )
    def test_special_chars(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Combined / integration scenarios
# ---------------------------------------------------------------------------


class TestCombined:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            (
                "Target 3.5km out, shields at 45%",
                "Target three point five kilometers out, shields at forty five percent",
            ),
            (
                "Bounty is 15000 aUEC, engage QT to Hurston",
                "Bounty is fifteen thousand alpha U E C, engage quantum travel to Hurston",
            ),
            (
                "**Warning:** Hull at 20% hull, range 800m",
                "Warning: Hull at twenty percent hull, range eight hundred meters",
            ),
        ],
    )
    def test_combined_scenarios(self, input_text: str, expected: str) -> None:
        assert preprocess_for_tts(input_text) == expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert preprocess_for_tts("") == ""

    def test_plain_text_passthrough(self) -> None:
        text = "Check your six, we have traffic."
        assert preprocess_for_tts(text) == text

    def test_multiple_spaces_collapsed(self) -> None:
        assert preprocess_for_tts("too   many   spaces") == "too many spaces"

    def test_newlines_become_sentence_breaks(self) -> None:
        result = preprocess_for_tts("Line one\nLine two")
        assert result == "Line one. Line two"

    def test_bullet_list_conversion(self) -> None:
        text = "Items:\n- First\n- Second"
        result = preprocess_for_tts(text)
        # Bullets become sentence-break pauses; newlines also become periods
        assert "First" in result
        assert "Second" in result
        assert "-" not in result
