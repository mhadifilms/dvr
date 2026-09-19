#!/usr/bin/env python3
"""Verify dvr's hand-coded enum tables against a running DaVinci Resolve.

`dvr` keeps Resolve's magic numbers in explicit Python tables so building a
payload never has to open a connection. The cost of that choice is drift: the
table is right only until Blackmagic changes the API, and a missing entry
turns into `dvr` rejecting something Resolve supports.

Version 1.6.4 shipped exactly that bug. Its caption table held 16 of the 26
languages Resolve 21.1 accepts, so `finnish`, `hindi`, `indonesian`, `malay`,
`polish`, `romanian`, `turkish`, `vietnamese`, `tamil` and `thai` were
unreachable through `dvr` even though the application handled them.

This script closes that loop. It checks two directions:

*Correctness* -- every value `dvr` hard-codes matches the live constant.
*Coverage*    -- every constant the live application exposes is in the table.

Coverage probes a candidate name list, because Resolve's scripting object does
not enumerate its constants through ``dir()``. Adding a plausible name to
``_LANGUAGE_CANDIDATES`` is how a future language gets caught.

Requires a running Resolve. Exits 1 on any drift, so it can gate a release:

    python scripts/check_api_truth.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dvr import Resolve  # noqa: E402
from dvr.subtitles import _BREAKS, _LANGUAGES, _PRESETS  # noqa: E402

#: Names probed for coverage. A language Resolve gains later is only detected
#: once its English name appears here, so keep this list generous.
_LANGUAGE_CANDIDATES = (
    "AUTO",
    "ARABIC",
    "BENGALI",
    "BULGARIAN",
    "CATALAN",
    "CROATIAN",
    "CZECH",
    "DANISH",
    "DUTCH",
    "ENGLISH",
    "ESTONIAN",
    "FILIPINO",
    "FINNISH",
    "FRENCH",
    "GERMAN",
    "GREEK",
    "HEBREW",
    "HINDI",
    "HUNGARIAN",
    "ICELANDIC",
    "INDONESIAN",
    "ITALIAN",
    "JAPANESE",
    "KANNADA",
    "KOREAN",
    "LATVIAN",
    "LITHUANIAN",
    "MALAY",
    "MALAYALAM",
    "MANDARIN_SIMPLIFIED",
    "MANDARIN_TRADITIONAL",
    "MARATHI",
    "NORWEGIAN",
    "PERSIAN",
    "POLISH",
    "PORTUGUESE",
    "PUNJABI",
    "ROMANIAN",
    "RUSSIAN",
    "SERBIAN",
    "SLOVAK",
    "SLOVENIAN",
    "SPANISH",
    "SWEDISH",
    "TAMIL",
    "TELUGU",
    "THAI",
    "TURKISH",
    "UKRAINIAN",
    "URDU",
    "VIETNAMESE",
)

_MISSING = object()

#: Table keys that are friendly aliases for another key's constant, so they
#: have no constant of their own to check.
_ALIASES = {"default"}


def _constant(raw: Any, name: str) -> Any:
    """Read a Resolve scripting constant, or ``_MISSING``.

    The scripting bridge answers unknown attributes with ``None`` instead of
    raising, so a plain ``getattr`` default never fires.
    """
    value = getattr(raw, name, None)
    if value is None:
        return _MISSING
    # The bridge hands back floats for integer enums.
    return int(value) if isinstance(value, (int, float)) else value


def _table_key_to_constant(key: str) -> str:
    special = {
        "mandarinsimplified": "MANDARIN_SIMPLIFIED",
        "mandarintraditional": "MANDARIN_TRADITIONAL",
        "subtitledefault": "SUBTITLE_DEFAULT",
    }
    return special.get(key, key.upper())


def check_correctness(raw: Any, table: dict[str, int], prefix: str, label: str) -> list[str]:
    """Every value dvr hard-codes must equal the live constant."""
    problems = []
    for key, expected in sorted(table.items(), key=lambda kv: kv[1]):
        if key in _ALIASES:
            continue
        name = f"{prefix}{_table_key_to_constant(key)}"
        live = _constant(raw, name)
        if live is _MISSING:
            problems.append(f"{label}: dvr has {key!r}={expected} but Resolve has no {name}")
        elif live != expected:
            problems.append(f"{label}: {key!r} is {expected} in dvr, {live} in Resolve ({name})")
    return problems


def check_coverage(
    raw: Any, table: dict[str, int], prefix: str, candidates: tuple[str, ...], label: str
) -> list[str]:
    """Every constant the live application exposes must be in dvr's table."""
    known = {_table_key_to_constant(key) for key in table}
    problems = []
    for candidate in candidates:
        if candidate in known:
            continue
        live = _constant(raw, f"{prefix}{candidate}")
        if live is not _MISSING:
            problems.append(
                f"{label}: Resolve exposes {prefix}{candidate}={live} but dvr's table omits it"
            )
    return problems


def main() -> int:
    try:
        resolve = Resolve()
    except Exception as exc:
        print(f"error: could not connect to DaVinci Resolve: {exc}", file=sys.stderr)
        print("This check needs Resolve running. Start it and re-run.", file=sys.stderr)
        return 2

    raw = resolve.raw
    print(f"Checking dvr enum tables against Resolve {resolve.app.version}\n")

    problems: list[str] = []
    problems += check_correctness(raw, _LANGUAGES, "AUTO_CAPTION_", "caption language")
    problems += check_coverage(
        raw, _LANGUAGES, "AUTO_CAPTION_", _LANGUAGE_CANDIDATES, "caption language"
    )
    problems += check_correctness(raw, _PRESETS, "AUTO_CAPTION_", "caption preset")
    problems += check_correctness(raw, _BREAKS, "AUTO_CAPTION_LINE_", "caption line break")

    subtitle_keys = {
        "SUBTITLE_LANGUAGE": 0,
        "SUBTITLE_CAPTION_PRESET": 1,
        "SUBTITLE_CHARS_PER_LINE": 2,
        "SUBTITLE_LINE_BREAK": 3,
    }
    for name, expected in subtitle_keys.items():
        live = _constant(raw, name)
        if live is _MISSING:
            problems.append(f"subtitle setting key: Resolve has no {name}")
        elif live != expected:
            problems.append(
                f"subtitle setting key: dvr writes {name} as {expected}, Resolve says {live}"
            )

    checked = len(_LANGUAGES) + len(_PRESETS) + len(_BREAKS) + len(subtitle_keys)
    if problems:
        print(f"{len(problems)} problem(s) found:\n")
        for problem in problems:
            print(f"  - {problem}")
        print("\nUpdate the tables in dvr/subtitles.py to match the live application.")
        return 1

    print(f"OK: {checked} constants match, {len(_LANGUAGE_CANDIDATES)} language names probed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
