from types import SimpleNamespace

import pytest

from dvr.color import ColorOps
from dvr.errors import ColorError


def test_cdl_matches_resolve_documented_payload():
    calls = []
    raw = SimpleNamespace(SetCDL=lambda value: calls.append(value) or True)
    color = ColorOps(SimpleNamespace(raw=raw, name="Test"))
    color.set_cdl(
        node_index=4,
        slope=(0.5, 0.4, 0.2),
        offset=(0.4, 0.3, 0.2),
        power=(0.6, 0.7, 0.8),
        saturation=0.65,
    )
    assert calls == [
        {
            "NodeIndex": "4",
            "Slope": "0.5 0.4 0.2",
            "Offset": "0.4 0.3 0.2",
            "Power": "0.6 0.7 0.8",
            "Saturation": "0.65",
        }
    ]


def test_legacy_neutral_master_preserves_rgb_values():
    calls = []
    color = ColorOps(
        SimpleNamespace(
            raw=SimpleNamespace(SetCDL=lambda value: calls.append(value) or True), name="Test"
        )
    )
    color.set_cdl(slope=(1.1, 1, 0.9, 1), offset=(0, -0.003, 0.01, 0), power=(1, 1, 1, 1))
    assert calls[0] == {
        "NodeIndex": "1",
        "Slope": "1.1 1 0.9",
        "Offset": "0 -0.003 0.01",
        "Power": "1 1 1",
    }


@pytest.mark.parametrize(
    "values",
    [
        {"slope": (1, 1, 1, 0.9)},
        {"offset": (0, 0, 0, 1)},
        {"power": (1, 1, float("nan"))},
        {"saturation": float("inf")},
        {"node_index": 0},
    ],
)
def test_invalid_cdl_never_calls_native_api(values):
    color = ColorOps(
        SimpleNamespace(
            raw=SimpleNamespace(SetCDL=lambda value: pytest.fail("invalid native call")),
            name="Test",
        )
    )
    with pytest.raises(ColorError):
        color.set_cdl(**values)


def test_native_rejection_remains_visible():
    color = ColorOps(SimpleNamespace(raw=SimpleNamespace(SetCDL=lambda value: False), name="Test"))
    with pytest.raises(ColorError, match="SetCDL returned False"):
        color.set_cdl(slope=(1, 1, 1))
