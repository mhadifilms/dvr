"""Switches must not discard unsaved edit/Fusion state."""

import pytest

from dvr import errors
from dvr.project import Project, ProjectNamespace
from dvr.timeline import Timeline

from .conftest import MockNode


@pytest.fixture
def scene(mock_resolve):
    events = []
    pm = mock_resolve.project_manager
    raw = mock_resolve.project
    old = mock_resolve.timeline
    old.responses["GetUniqueId"] = "old"
    new = MockNode("New", {"GetName": "New", "GetUniqueId": "new"})
    raw.responses["GetTimelineCount"] = 2
    raw.responses["GetTimelineByIndex"] = lambda i: old if i == 1 else new

    def save():
        events.append("save")
        return True

    def switch(target):
        events.append("switch:" + target.GetName())
        raw.responses["GetCurrentTimeline"] = target
        return True

    pm.responses["SaveProject"] = save
    raw.responses["SetCurrentTimeline"] = switch
    return Project(raw, pm), new, events, mock_resolve


def test_timeline_use_saves_before_entry_and_restore(scene):
    p, _, events, _ = scene
    with p.timeline.use("New"):
        events.append("edit")
    assert events == ["save", "switch:New", "edit", "save", "switch:MockTimeline"]


def test_same_timeline_is_noop(scene):
    p, _, events, _ = scene
    p.timeline.set_current("MockTimeline")
    assert events == []


def test_failed_save_blocks_timeline_switch(scene):
    p, _, events, mock = scene
    mock.project_manager.responses["SaveProject"] = False
    with pytest.raises(errors.ProjectError):
        p.timeline.set_current("New")
    assert events == []
    assert p.timeline.current.name == "MockTimeline"


def test_restore_save_failure_is_not_swallowed(scene):
    p, _, events, mock = scene
    with pytest.raises(errors.ProjectError), p.timeline.use("New"):
        mock.project_manager.responses["SaveProject"] = False
    assert events == ["save", "switch:New"]
    assert p.timeline.current.name == "New"


@pytest.mark.parametrize("operation", ["load", "create", "create_cloud_project", "close"])
@pytest.mark.parametrize("saved", [True, False])
def test_project_changes_save_first(scene, operation, saved):
    p, _, events, mock = scene
    pm = mock.project_manager
    ns = ProjectNamespace(mock, pm)
    method = {
        "load": "LoadProject",
        "create": "CreateProject",
        "create_cloud_project": "CreateCloudProject",
        "close": "CloseProject",
    }[operation]

    def mutate(*args):
        events.append(operation)
        return True if operation == "close" else mock.project

    pm.responses[method] = mutate
    if not saved:
        pm.responses["SaveProject"] = False
    action = p.close if operation == "close" else lambda: getattr(ns, operation)("New")
    if saved:
        action()
        assert events == ["save", operation]
    else:
        with pytest.raises(errors.ProjectError):
            action()
        assert events == []


@pytest.mark.parametrize("operation", ["create", "empty", "clips", "import", "duplicate"])
@pytest.mark.parametrize("saved", [True, False])
def test_implicit_timeline_switches_save_first(scene, operation, saved):
    p, new, events, mock = scene
    pool = mock.project.responses["GetMediaPool"]

    def mutate(*args):
        events.append(operation)
        return new

    pool.responses.update(
        {
            "CreateEmptyTimeline": mutate,
            "CreateTimelineFromClips": mutate,
            "ImportTimelineFromFile": mutate,
        }
    )
    mock.timeline.responses["DuplicateTimeline"] = mutate
    actions = {
        "create": lambda: p.timeline.create("New"),
        "empty": lambda: p.media.create_empty_timeline("New"),
        "clips": lambda: p.media.create_timeline_from_clips("New", []),
        "import": lambda: p.media.import_timeline("new.drt"),
        "duplicate": lambda: p.timeline.current.duplicate("New"),
    }
    if saved:
        assert isinstance(actions[operation](), Timeline)
        assert events == ["save", operation]
    else:
        mock.project_manager.responses["SaveProject"] = False
        with pytest.raises(errors.ProjectError):
            actions[operation]()
        assert events == []


def test_raw_only_wrapper_refuses_unsafe_switch(scene):
    p, _, events, _ = scene
    with pytest.raises(errors.ProjectError, match="project-manager"):
        Project(p.raw, None).timeline.set_current("New")
    assert events == []


def test_same_project_does_not_save_or_reload(scene):
    _, _, events, mock = scene
    ns = ProjectNamespace(mock, mock.project_manager)
    ns.load("MockProject")
    assert events == []
    assert not any(c[0] == "LoadProject" for c in mock.project_manager.calls)


def test_project_context_saves_before_restore(scene):
    _, _, events, mock = scene
    pm = mock.project_manager
    pm.responses["GetProjectListInCurrentFolder"] = ["MockProject", "New"]

    def load(name):
        events.append("load:" + name)
        current = MockNode(name, {"GetName": name})
        pm.responses["GetCurrentProject"] = current
        return current

    pm.responses["LoadProject"] = load
    with ProjectNamespace(mock, pm).use("New"):
        events.append("edit")
    assert events == ["save", "load:New", "edit", "save", "load:MockProject"]
