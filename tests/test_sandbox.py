"""The restricted eval tier must not be escapable through the usual routes."""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.sandbox import SAFE_BUILTINS, restricted_eval


def test_plain_expressions_still_work():
    assert restricted_eval("1 + 1", {}) == 2
    assert restricted_eval("sorted([3, 1, 2])", {}) == [1, 2, 3]
    assert restricted_eval("len(name)", {"name": "abcd"}) == 4


def test_bound_names_are_reachable():
    marker = object()
    assert restricted_eval("r", {"r": marker}) is marker


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').getcwd()",
        "(1).__class__.__bases__[0].__subclasses__()",
        "r.__class__.__init__.__globals__",
    ],
)
def test_dunder_access_is_refused(expression):
    with pytest.raises(errors.DvrError, match="Dunder attribute access"):
        restricted_eval(expression, {"r": object()})


@pytest.mark.parametrize(
    "expression", ["open('/etc/hosts')", "eval('1')", "exec('x=1')", "compile('1','','eval')"]
)
def test_dangerous_builtins_are_absent(expression):
    # NameError, not a DvrError: the name simply does not exist in scope.
    with pytest.raises(NameError):
        restricted_eval(expression, {})


def test_allowlist_excludes_the_import_machinery():
    for forbidden in ("__import__", "open", "eval", "exec", "compile", "input", "globals", "vars"):
        assert forbidden not in SAFE_BUILTINS


def test_caller_namespace_is_not_mutated():
    namespace: dict[str, object] = {"r": None}
    restricted_eval("r", namespace)
    assert namespace == {"r": None}, "restricted_eval must not leak __builtins__ back to the caller"
