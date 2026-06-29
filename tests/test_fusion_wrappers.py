from __future__ import annotations

import pytest

from dvr import errors
from dvr.timeline import FusionComp, FusionTool, TimelineItem


class _FakeToolRaw:
    def __init__(self, *, tool_id: str = "Tool.ID", name: str = "Tool1") -> None:
        self.ID = tool_id
        self.Name = name
        self.inputs: dict[tuple[str, int | None], object] = {}
        self.connect_calls: list[tuple[object, ...]] = []
        self.set_result: bool | None = True
        self.connect_result: bool = True

    def SetInput(self, key: str, value: object, frame: int | None = None) -> bool | None:
        self.inputs[(key, frame)] = value
        return self.set_result

    def GetInput(self, key: str, frame: int | None = None) -> object:
        return self.inputs.get((key, frame))

    def ConnectInput(self, *args: object) -> bool:
        self.connect_calls.append(args)
        return self.connect_result


class _FakeCompRaw:
    def __init__(self) -> None:
        self.add_calls: list[tuple[str, float, float]] = []
        self.tools_by_name: dict[str, _FakeToolRaw] = {"MediaIn1": _FakeToolRaw(name="MediaIn1")}
        self.add_result: _FakeToolRaw | None = _FakeToolRaw(tool_id="ofx.example", name="OFX1")

    def GetToolList(self, _selected_only: bool) -> dict[str, _FakeToolRaw]:
        return self.tools_by_name

    def FindTool(self, name: str) -> _FakeToolRaw | None:
        return self.tools_by_name.get(name)

    def AddTool(self, tool_id: str, x: float, y: float) -> _FakeToolRaw | None:
        self.add_calls.append((tool_id, x, y))
        return self.add_result


class _FakeItemRaw:
    def __init__(self) -> None:
        self.comp = _FakeCompRaw()
        self.added = False

    def GetName(self) -> str:
        return "Shot001"

    def GetFusionCompNameList(self) -> list[str]:
        return ["Comp1"]

    def GetFusionCompByIndex(self, index: int) -> _FakeCompRaw | None:
        return self.comp if index == 1 else None

    def AddFusionComp(self) -> _FakeCompRaw:
        self.added = True
        return self.comp

    def LoadFusionCompByName(self, name: str) -> _FakeCompRaw | None:
        return self.comp if name == "Comp1" else None


def test_fusion_comp_add_tool_wraps_raw_tool() -> None:
    raw = _FakeCompRaw()
    comp = FusionComp(raw)

    tool = comp.add_tool("ofx.test.Plugin", x=2, y=3)

    assert isinstance(tool, FusionTool)
    assert tool.id == "ofx.example"
    assert tool.name == "OFX1"
    assert raw.add_calls == [("ofx.test.Plugin", 2, 3)]


def test_fusion_comp_add_tool_raises_when_resolve_rejects() -> None:
    raw = _FakeCompRaw()
    raw.add_result = None

    with pytest.raises(errors.FusionError):
        FusionComp(raw).add_tool("missing.Plugin")


def test_fusion_tool_inputs_and_connections() -> None:
    source_raw = _FakeToolRaw(tool_id="MediaIn", name="MediaIn1")
    target_raw = _FakeToolRaw(tool_id="OFX", name="Effect1")
    source = FusionTool(source_raw)
    target = FusionTool(target_raw)

    target.set_input("gain", 1.25)
    target.connect_input("Source", source)

    assert target.get_input("gain") == 1.25
    assert target_raw.connect_calls == [("Source", source_raw, "Output")]


def test_timeline_item_fusion_wrapped_comp_access() -> None:
    item = TimelineItem(_FakeItemRaw(), track_type="video", track_index=1)

    existing = item.fusion.require_comp()
    created = item.fusion.add_comp()
    loaded = item.fusion.load_comp("Comp1")

    assert isinstance(existing, FusionComp)
    assert isinstance(created, FusionComp)
    assert isinstance(loaded, FusionComp)
