"""Tests for Text+ (title) customization, TTS params, and subtitle surfaces."""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.timeline import ItemText, Timeline, TimelineItem, _parse_color

# ---------------------------------------------------------------------------
# Fakes — a Text+ tool inside a Fusion comp on a timeline item.
# ---------------------------------------------------------------------------


class _FakeTextTool:
    def __init__(self, *, tool_id: str = "TextPlus", name: str = "Template") -> None:
        self.ID = tool_id
        self.Name = name
        self.inputs: dict[str, object] = {}

    def SetInput(self, key: str, value: object, frame: int | None = None) -> bool:
        self.inputs[key] = value
        return True

    def GetInput(self, key: str, frame: int | None = None) -> object:
        return self.inputs.get(key)

    def GetAttrs(self) -> dict[str, object]:
        return {"TOOLS_RegID": self.ID}


class _FakeComp:
    def __init__(self, text_tool: _FakeTextTool | None) -> None:
        self._text_tool = text_tool

    def GetToolList(self, _selected: bool, reg_id: str | None = None) -> dict[int, _FakeTextTool]:
        if self._text_tool is None:
            return {}
        if reg_id in (None, "TextPlus"):
            return {1: self._text_tool}
        return {}

    def FindTool(self, name: str) -> _FakeTextTool | None:
        return self._text_tool


class _FakeTitleItem:
    def __init__(self, text_tool: _FakeTextTool | None) -> None:
        self._comp = _FakeComp(text_tool)

    def GetName(self) -> str:
        return "Text+"

    def GetFusionCompByIndex(self, index: int) -> _FakeComp | None:
        return self._comp if index == 1 else None


class _FakeTimelineRaw:
    def __init__(self, item_raw: _FakeTitleItem) -> None:
        self._item_raw = item_raw
        self.inserted: list[str] = []

    def GetName(self) -> str:
        return "MockTimeline"

    def InsertFusionTitleIntoTimeline(self, title: str) -> _FakeTitleItem | None:
        self.inserted.append(title)
        return self._item_raw

    def GetTrackCount(self, _kind: str) -> int:
        return 1

    def GetItemListInTrack(self, _kind: str, _idx: int) -> list[object]:
        return []


def _text_item(text_tool: _FakeTextTool | None = None) -> TimelineItem:
    tool = _FakeTextTool() if text_tool is None else text_tool
    return TimelineItem(_FakeTitleItem(tool), track_type="video", track_index=1)


# ---------------------------------------------------------------------------
# Color parsing
# ---------------------------------------------------------------------------


def test_parse_color_hex() -> None:
    assert _parse_color("#ff0000") == (1.0, 0.0, 0.0, None)


def test_parse_color_hex_with_alpha() -> None:
    r, g, b, a = _parse_color("#00ff00ff")
    assert (r, g, b) == (0.0, 1.0, 0.0)
    assert a == 1.0


def test_parse_color_name() -> None:
    assert _parse_color("white") == (1.0, 1.0, 1.0, None)


def test_parse_color_float_tuple() -> None:
    assert _parse_color((0.25, 0.5, 0.75)) == (0.25, 0.5, 0.75, None)


def test_parse_color_int_sequence_scales_from_255() -> None:
    r, g, b, _ = _parse_color([255, 0, 128])
    assert r == 1.0
    assert g == 0.0
    assert round(b, 3) == round(128 / 255.0, 3)


def test_parse_color_invalid_raises() -> None:
    with pytest.raises(errors.FusionError):
        _parse_color("not-a-color")


# ---------------------------------------------------------------------------
# ItemText
# ---------------------------------------------------------------------------


def test_item_text_set_maps_inputs() -> None:
    tool = _FakeTextTool()
    item = _text_item(tool)

    returned = item.text.set(
        "HELLO",
        font="Open Sans",
        style="Bold",
        size=0.12,
        color="#ff0000",
        opacity=0.8,
        tracking=1.1,
        line_spacing=1.2,
        position=(0.5, 0.25),
        align="center",
        vertical_align="top",
    )

    assert returned is item
    assert tool.inputs["StyledText"] == "HELLO"
    assert tool.inputs["Font"] == "Open Sans"
    assert tool.inputs["Style"] == "Bold"
    assert tool.inputs["Size"] == 0.12
    assert tool.inputs["Red1"] == 1.0
    assert tool.inputs["Green1"] == 0.0
    assert tool.inputs["Blue1"] == 0.0
    assert tool.inputs["Alpha1"] == 0.8
    assert tool.inputs["Tracking"] == 1.1
    assert tool.inputs["LineSpacing"] == 1.2
    assert tool.inputs["Center"] == [0.5, 0.25]
    assert tool.inputs["HorizontalAnchor"] == 0
    assert tool.inputs["VerticalAnchor"] == 1


def test_item_text_value_roundtrip() -> None:
    item = _text_item()
    item.text.value = "Caption"
    assert item.text.value == "Caption"


def test_item_text_properties_snapshot() -> None:
    item = _text_item()
    item.text.set("Hi", size=0.1)
    props = item.text.properties()
    assert props["text"] == "Hi"
    assert props["size"] == 0.1


def test_item_text_align_alias_int() -> None:
    tool = _FakeTextTool()
    item = _text_item(tool)
    item.text.set(align="right", vertical_align="bottom")
    assert tool.inputs["HorizontalAnchor"] == 1
    assert tool.inputs["VerticalAnchor"] == -1


def test_item_text_without_tool_raises() -> None:
    item = TimelineItem(_FakeTitleItem(None), track_type="video", track_index=1)
    with pytest.raises(errors.FusionError):
        item.text.set("nope")


def test_item_text_unknown_align_raises() -> None:
    item = _text_item()
    with pytest.raises(errors.FusionError):
        item.text.set(align="diagonal")


# ---------------------------------------------------------------------------
# Timeline.insert_title
# ---------------------------------------------------------------------------


def test_insert_title_inserts_and_styles() -> None:
    tool = _FakeTextTool()
    item_raw = _FakeTitleItem(tool)
    tl = Timeline(_FakeTimelineRaw(item_raw), project=object())

    item = tl.insert_title("Text+", text="HELLO", color="white", size=0.2)

    assert isinstance(item, TimelineItem)
    assert tl.raw.inserted == ["Text+"]
    assert tool.inputs["StyledText"] == "HELLO"
    assert tool.inputs["Red1"] == 1.0
    assert tool.inputs["Size"] == 0.2


def test_insert_title_missing_method_raises() -> None:
    class _NoTitleSupport:
        def GetName(self) -> str:
            return "MockTimeline"

    tl = Timeline(_NoTitleSupport(), project=object())
    with pytest.raises(errors.TimelineError):
        tl.insert_title("Text+")


def test_insert_title_returns_none_raises() -> None:
    class _NullInsert:
        def GetName(self) -> str:
            return "MockTimeline"

        def InsertFusionTitleIntoTimeline(self, _title: str) -> None:
            return None

    tl = Timeline(_NullInsert(), project=object())
    with pytest.raises(errors.TimelineError):
        tl.insert_title("Unknown Title")


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------


def test_mcp_exposes_text_tools() -> None:
    from dvr.mcp import server

    names = {spec.name for spec in server.list_tool_specs()}
    assert {"timeline_add_title", "clip_set_text", "timeline_create_subtitles"} <= names


def test_mcp_generate_speech_has_extended_params() -> None:
    from dvr.mcp import server

    spec = next(s for s in server.list_tool_specs() if s.name == "project_generate_speech")
    props = spec.schema["properties"]
    assert {"speed", "pitch", "filename"} <= set(props)


def test_mcp_add_title_schema_fields() -> None:
    from dvr.mcp import server

    spec = next(s for s in server.list_tool_specs() if s.name == "timeline_add_title")
    props = spec.schema["properties"]
    assert {"text", "font", "size", "color", "align", "vertical_align"} <= set(props)


def test_item_text_exported() -> None:
    import dvr

    assert dvr.ItemText is ItemText


# ---------------------------------------------------------------------------
# is_text
# ---------------------------------------------------------------------------


def test_is_text_true_for_title() -> None:
    assert _text_item().is_text is True


def test_is_text_false_without_tool() -> None:
    item = TimelineItem(_FakeTitleItem(None), track_type="video", track_index=1)
    assert item.is_text is False


# ---------------------------------------------------------------------------
# Declarative spec: titles
# ---------------------------------------------------------------------------


class _FakeSpecText:
    def __init__(self, holder: _FakeSpecItem) -> None:
        self._holder = holder

    @property
    def value(self) -> str:
        return self._holder.text_value

    def set(self, text: str | None = None, **kwargs: object) -> _FakeSpecItem:
        if text is not None:
            self._holder.text_value = text
        self._holder.styled.update(kwargs)
        return self._holder


class _FakeSpecItem:
    def __init__(self, text: str) -> None:
        self.text_value = text
        self.styled: dict[str, object] = {}
        self.is_text = True

    @property
    def text(self) -> _FakeSpecText:
        return _FakeSpecText(self)


class _FakeSpecTimeline:
    def __init__(self, name: str) -> None:
        self.name = name
        self._items: list[_FakeSpecItem] = []
        self.inserted: list[tuple[str, str | None, dict[str, object]]] = []
        self.current_timecode: str | None = None

    def set_setting(self, *_args: object) -> None:
        pass

    def markers(self) -> dict[int, object]:
        return {}

    def items(self, _track: str | None = None) -> list[_FakeSpecItem]:
        return list(self._items)

    def insert_title(
        self,
        title: str = "Text+",
        *,
        fusion: bool = True,
        text: str | None = None,
        **kwargs: object,
    ) -> _FakeSpecItem:
        item = _FakeSpecItem(text or "")
        item.styled.update(kwargs)
        self._items.append(item)
        self.inserted.append((title, text, dict(kwargs)))
        return item


class _FakeTimelineNS:
    def __init__(self) -> None:
        self.by_name: dict[str, _FakeSpecTimeline] = {}

    def ensure(self, name: str) -> _FakeSpecTimeline:
        return self.by_name.setdefault(name, _FakeSpecTimeline(name))


class _FakeProject:
    def __init__(self) -> None:
        self.name = "P"
        self.timeline = _FakeTimelineNS()

    def set_setting(self, *_args: object) -> None:
        pass


class _FakeProjectNS:
    def __init__(self, project: _FakeProject) -> None:
        self.project = project

    def list(self) -> list[str]:
        return ["P"]

    def ensure(self, _name: str) -> _FakeProject:
        return self.project


class _FakeResolve:
    def __init__(self) -> None:
        self.project = _FakeProjectNS(_FakeProject())


def _title_spec_data() -> dict[str, object]:
    return {
        "project": "P",
        "timelines": [
            {
                "name": "T",
                "titles": [{"text": "HELLO", "at": "01:00:02:00", "size": 0.12, "color": "white"}],
            }
        ],
    }


def test_parse_spec_titles() -> None:
    from dvr import spec as spec_mod

    parsed = spec_mod.parse_spec(_title_spec_data())
    titles = parsed.timelines[0].titles
    assert len(titles) == 1
    assert titles[0].text == "HELLO"
    assert titles[0].at == "01:00:02:00"
    assert titles[0].styling == {"size": 0.12, "color": "white"}


def test_parse_spec_title_requires_text() -> None:
    from dvr import spec as spec_mod

    with pytest.raises(errors.SpecError):
        spec_mod.parse_spec(
            {"project": "P", "timelines": [{"name": "T", "titles": [{"size": 0.1}]}]}
        )


def test_spec_plan_includes_titles() -> None:
    from dvr import spec as spec_mod

    parsed = spec_mod.parse_spec(_title_spec_data())
    actions = spec_mod.plan(parsed, _FakeResolve())  # type: ignore[arg-type]
    assert any(a.target.endswith("/title:HELLO") for a in actions)


def test_spec_apply_titles_idempotent() -> None:
    from dvr import spec as spec_mod

    parsed = spec_mod.parse_spec(_title_spec_data())
    r = _FakeResolve()
    spec_mod.apply(parsed, r, run_hooks=False)  # type: ignore[arg-type]
    tl = r.project.project.timeline.by_name["T"]
    assert len(tl.inserted) == 1
    assert tl.inserted[0][1] == "HELLO"
    assert tl.current_timecode == "01:00:02:00"

    # Re-applying the same spec must not insert a second copy.
    spec_mod.apply(parsed, r, run_hooks=False)  # type: ignore[arg-type]
    assert len(tl.inserted) == 1
    assert tl._items[0].styled["color"] == "white"
