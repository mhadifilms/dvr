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
