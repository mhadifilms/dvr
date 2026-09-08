"""Resolve Auto Caption settings, as documented by the installed scripting API.

The native keys and enum values are numeric constants, not UI label strings.
Values below were verified against Resolve 21.0.4. As with clip-property enums,
keep the values explicit so constructing a payload does not open a connection.
"""

from __future__ import annotations

from . import errors

_LANGUAGES = {
    "auto": 0,
    "mandarinsimplified": 1,
    "dutch": 2,
    "english": 3,
    "french": 5,
    "german": 6,
    "italian": 9,
    "japanese": 10,
    "korean": 11,
    "norwegian": 13,
    "portuguese": 15,
    "russian": 17,
    "spanish": 18,
    "swedish": 19,
    "danish": 24,
    "mandarintraditional": 25,
}
_LANGUAGE_CODES = {
    "da": "danish",
    "nl": "dutch",
    "en": "english",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "ja": "japanese",
    "ko": "korean",
    "no": "norwegian",
    "pt": "portuguese",
    "ru": "russian",
    "es": "spanish",
    "sv": "swedish",
    "zhhans": "mandarinsimplified",
    "zhhant": "mandarintraditional",
}
_PRESETS = {"default": 0, "subtitledefault": 0, "teletext": 1, "netflix": 2}
_BREAKS = {"single": 1, "double": 2}


def _normalized(value: str) -> str:
    return value.casefold().replace("_", "").replace("-", "").replace(" ", "")


def caption_settings(
    *,
    language: str,
    chars_per_line: int,
    line_break_type: str,
    preset: str | None,
) -> dict[int, int]:
    """Validate friendly names and return Resolve's native caption dictionary."""
    name = _normalized(language)
    name = _LANGUAGE_CODES.get(name, name)
    if name not in _LANGUAGES:
        raise errors.TimelineError(
            f"Unsupported subtitle language {language!r}.",
            fix="Use auto, a documented language name, or its supported language code.",
            state={"supported": sorted(_LANGUAGES)},
        )
    if type(chars_per_line) is not int or not 1 <= chars_per_line <= 60:
        raise errors.TimelineError("Subtitle characters per line must be an integer from 1 to 60.")
    # SUBTITLE_LANGUAGE=0, SUBTITLE_CHARS_PER_LINE=2.
    result = {0: _LANGUAGES[name], 2: chars_per_line}
    line_break = _normalized(line_break_type)
    if line_break != "auto":
        if line_break not in _BREAKS:
            raise errors.TimelineError("Subtitle line break must be Auto, Single, or Double.")
        result[3] = _BREAKS[line_break]  # SUBTITLE_LINE_BREAK
    if preset is not None:
        selected = _normalized(preset)
        if selected not in _PRESETS:
            raise errors.TimelineError("Subtitle preset must be Default, Teletext, or Netflix.")
        result[1] = _PRESETS[selected]  # SUBTITLE_CAPTION_PRESET
    # Auto omits the line-break setting so Resolve retains the preset/UI default.
    return result
