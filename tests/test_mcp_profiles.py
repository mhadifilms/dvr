"""Tool profiles must shrink the listing without removing any capability."""

from __future__ import annotations

import json

import pytest

from dvr.mcp import server


@pytest.fixture(autouse=True)
def default_profile(monkeypatch):
    monkeypatch.delenv("DVR_MCP_PROFILE", raising=False)


def _payload(specs) -> int:
    return len(
        json.dumps(
            [
                {"name": s.name, "description": s.description, "input_schema": s.schema}
                for s in specs
            ]
        )
    )


def test_core_is_the_default_profile():
    assert server.active_profile() == "core"


def test_unknown_profile_falls_back_to_core(monkeypatch):
    monkeypatch.setenv("DVR_MCP_PROFILE", "enormous")
    assert server.active_profile() == "core"


def test_full_profile_lists_everything(monkeypatch):
    monkeypatch.setenv("DVR_MCP_PROFILE", "full")
    specs = server.list_tool_specs()
    assert server.profiled_specs(specs) == specs


def test_core_profile_is_a_strict_subset():
    specs = server.list_tool_specs()
    core = server.profiled_specs(specs)
    assert 0 < len(core) < len(specs)
    assert {s.name for s in core} <= {s.name for s in specs}


def test_core_profile_meaningfully_cuts_the_payload():
    specs = server.list_tool_specs()
    core = server.profiled_specs(specs)
    # The whole point is context cost, so hold the line on it.
    assert _payload(core) < _payload(specs) / 2


def test_every_core_name_exists_in_the_registry():
    names = {s.name for s in server.list_tool_specs()}
    assert names >= server.CORE_TOOLS


def test_tool_search_is_always_listed():
    assert "tool_search" in {s.name for s in server.profiled_specs(server.list_tool_specs())}


# --- tool_search -----------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("dctl", "dctl_write"),
        ("cdl", "color_set_cdl"),
        ("subtitles", "timeline_create_subtitles"),
        ("magic mask deblur", "media_deblur"),
    ],
)
def test_tool_search_finds_the_relevant_tool(query, expected):
    found = server._h_tool_search(None, {"query": query, "limit": 10})
    assert expected in {m["name"] for m in found["matches"]}


def test_tool_search_reports_schemas_for_unlisted_tools():
    found = server._h_tool_search(None, {"query": "dctl write", "limit": 5})
    match = next(m for m in found["matches"] if m["name"] == "dctl_write")
    assert match["already_listed"] is False
    assert "content" in match["input_schema"]["properties"]


def test_tool_search_excludes_itself():
    found = server._h_tool_search(None, {"query": "search tools", "limit": 10})
    assert "tool_search" not in {m["name"] for m in found["matches"]}


def test_tool_search_respects_the_limit():
    found = server._h_tool_search(None, {"query": "timeline", "limit": 3})
    assert len(found["matches"]) <= 3


def test_tool_search_reports_the_real_totals():
    found = server._h_tool_search(None, {"query": "render", "limit": 1})
    assert found["total_tools"] == len(server.list_tool_specs())
    assert found["listed_tools"] == len(server.profiled_specs(server.list_tool_specs()))


def test_unlisted_tools_remain_dispatchable():
    """A narrowed listing must never narrow what the server can actually do."""
    specs = server.list_tool_specs()
    registry = {s.name: s for s in specs}
    unlisted = {s.name for s in specs} - {s.name for s in server.profiled_specs(specs)}
    assert unlisted, "core profile should be hiding something"
    # Dispatch is built from the complete registry, not the profiled listing.
    assert unlisted <= set(registry)


# --- new capability surface ------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "color_inspect",
        "color_set_cdl",
        "color_node_lut",
        "color_export_lut",
        "color_versions",
        "color_copy_grades",
        "color_reset",
        "dctl_list",
        "dctl_read",
        "dctl_write",
        "dctl_delete",
        "lut_list",
        "lut_generate",
        "lut_delete",
        "eval_unsafe",
    ],
)
def test_new_tools_are_registered(name):
    assert name in {s.name for s in server.list_tool_specs()}


def test_file_tools_do_not_require_a_resolve_connection():
    """LUT and DCTL files live on disk, so these must work with Resolve closed."""
    specs = {s.name: s for s in server.list_tool_specs()}
    for name in ("dctl_list", "dctl_write", "lut_list", "lut_generate", "lut_delete"):
        assert specs[name].needs_resolve is False


def test_color_tools_accept_the_shared_clip_selector():
    specs = {s.name: s for s in server.list_tool_specs()}
    for name in ("color_inspect", "color_set_cdl", "color_reset"):
        properties = specs[name].schema["properties"]
        assert {"timeline", "track_type", "track_index", "name_contains"} <= set(properties)


def test_eval_tiers_are_gated_separately(monkeypatch):
    from dvr import errors

    monkeypatch.delenv("DVR_MCP_ENABLE_EVAL", raising=False)
    monkeypatch.delenv("DVR_MCP_ENABLE_EVAL_UNSAFE", raising=False)
    with pytest.raises(errors.DvrError, match="disabled by default"):
        server._h_eval(None, {"expression": "1"})
    with pytest.raises(errors.DvrError, match="disabled by default"):
        server._h_eval_unsafe(None, {"expression": "1"})


def test_enabling_the_safe_tier_does_not_enable_the_unsafe_one(monkeypatch):
    from dvr import errors

    monkeypatch.setenv("DVR_MCP_ENABLE_EVAL", "1")
    monkeypatch.delenv("DVR_MCP_ENABLE_EVAL_UNSAFE", raising=False)
    with pytest.raises(errors.DvrError, match="disabled by default"):
        server._h_eval_unsafe(None, {"expression": "1"})


def test_eval_description_does_not_overpromise():
    """The old description claimed 'No imports' while imports in fact worked."""
    spec = next(s for s in server.list_tool_specs() if s.name == "eval")
    assert "can change the project" in spec.description
