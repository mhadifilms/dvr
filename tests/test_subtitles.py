from types import SimpleNamespace

import pytest

from dvr.errors import TimelineError
from dvr.timeline import Timeline


def timeline(ok=True):
    calls = []
    raw = SimpleNamespace(
        GetName=lambda: "Source review",
        CreateSubtitlesFromAudio=lambda payload: calls.append(payload) or ok,
    )
    return Timeline(raw, None), calls


def test_native_english_settings_and_preset():
    tl, calls = timeline()
    tl.create_subtitles_from_audio(
        language="English", chars_per_line=32, line_break_type="Double", preset="Netflix"
    )
    # Resolve 21 scripting constants: language=0, preset=1, chars=2, line break=3;
    # English=3, Netflix=2, double=2. No UI strings may cross this boundary.
    assert calls == [{0: 3, 1: 2, 2: 32, 3: 2}]


def test_auto_keeps_native_preset_line_break_default():
    tl, calls = timeline()
    tl.create_subtitles_from_audio()
    assert calls == [{0: 0, 2: 42}]


@pytest.mark.parametrize(
    "language, expected", [("en", 3), ("zh-Hant", 25), ("Mandarin Simplified", 1), ("da", 24)]
)
def test_language_codes_and_names(language, expected):
    tl, calls = timeline()
    tl.create_subtitles_from_audio(language=language, preset="Teletext", line_break_type="Single")
    assert calls == [{0: expected, 1: 1, 2: 42, 3: 1}]


@pytest.mark.parametrize(
    "arguments",
    [
        {"language": "invented"},
        {"chars_per_line": 0},
        {"chars_per_line": 61},
        {"chars_per_line": True},
        {"chars_per_line": 42.5},
        {"preset": "invented"},
        {"line_break_type": "Triple"},
    ],
)
def test_invalid_options_never_invoke_transcription(arguments):
    tl, calls = timeline()
    with pytest.raises(TimelineError):
        tl.create_subtitles_from_audio(**arguments)
    assert calls == []


def test_native_failure_remains_visible():
    tl, calls = timeline(ok=False)
    with pytest.raises(TimelineError, match="CreateSubtitlesFromAudio returned False"):
        tl.create_subtitles_from_audio(language="en")
    assert calls == [{0: 3, 2: 42}]


# ---------------------------------------------------------------------------
# Language coverage
#
# v1.6.4 shipped 16 of the 26 languages Resolve 21.1 accepts, so `hindi`,
# `turkish`, `thai` and seven others were rejected outright. The values below
# were read from the live application; `scripts/check_api_truth.py` re-checks
# them against a running Resolve.
# ---------------------------------------------------------------------------

LIVE_LANGUAGES = {
    "auto": 0,
    "mandarinsimplified": 1,
    "dutch": 2,
    "english": 3,
    "finnish": 4,
    "french": 5,
    "german": 6,
    "hindi": 7,
    "indonesian": 8,
    "italian": 9,
    "japanese": 10,
    "korean": 11,
    "malay": 12,
    "norwegian": 13,
    "polish": 14,
    "portuguese": 15,
    "romanian": 16,
    "russian": 17,
    "spanish": 18,
    "swedish": 19,
    "turkish": 20,
    "vietnamese": 21,
    "tamil": 22,
    "thai": 23,
    "danish": 24,
    "mandarintraditional": 25,
}


def test_every_resolve_language_is_reachable():
    from dvr.subtitles import _LANGUAGES

    assert _LANGUAGES == LIVE_LANGUAGES


@pytest.mark.parametrize("language, expected", sorted(LIVE_LANGUAGES.items()))
def test_each_language_maps_to_its_native_constant(language, expected):
    tl, calls = timeline()
    tl.create_subtitles_from_audio(language=language)
    assert calls == [{0: expected, 2: 42}]


@pytest.mark.parametrize(
    "code, expected",
    [
        ("fi", 4),
        ("hi", 7),
        ("id", 8),
        ("ms", 12),
        ("pl", 14),
        ("ro", 16),
        ("tr", 20),
        ("vi", 21),
        ("ta", 22),
        ("th", 23),
    ],
)
def test_language_codes_for_the_previously_missing_languages(code, expected):
    tl, calls = timeline()
    tl.create_subtitles_from_audio(language=code)
    assert calls == [{0: expected, 2: 42}]


def test_every_language_code_resolves_to_a_known_language():
    from dvr.subtitles import _LANGUAGE_CODES, _LANGUAGES

    assert set(_LANGUAGE_CODES.values()) <= set(_LANGUAGES)


def test_language_values_are_contiguous():
    """A gap means a language was skipped when the table was transcribed."""
    from dvr.subtitles import _LANGUAGES

    values = sorted(_LANGUAGES.values())
    assert values == list(range(len(values)))
