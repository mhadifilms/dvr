"""Restricted expression evaluation for agent-facing entry points.

`dvr eval` and the MCP `eval` tool let an agent reach the live object model
directly. Passing a plain dictionary to :func:`eval` does **not** contain
anything: CPython injects the full ``__builtins__`` into any globals mapping
that lacks the key, so ``__import__('subprocess')`` stays reachable.

This module closes that path. It supplies an explicit builtins allowlist and
rejects dunder attribute access, which is the usual route back to the
interpreter (``obj.__class__.__init__.__globals__``).

What it guarantees
------------------

* No imports, no filesystem, no network, no subprocesses.
* No access to dunder attributes, so the standard escapes are unavailable.

What it deliberately does not guarantee
---------------------------------------

Expressions still run against a live DaVinci Resolve. Anything the scripting
API can change -- deleting a timeline, clearing the render queue -- remains
reachable, because that is the point of the tool. This is a capability
boundary against the host, not against Resolve. Callers that need full Python
must use the separately gated unrestricted tier.
"""

from __future__ import annotations

import builtins
from typing import Any

from . import errors

#: Builtins an expression may use. Everything that can reach the import
#: machinery, the filesystem, or the interpreter internals is omitted.
SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in (
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "hasattr",
        "int",
        "isinstance",
        "issubclass",
        "len",
        "list",
        "map",
        "max",
        "min",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    )
}

_ESCAPE_HINT = (
    "Remove dunder attribute access. Use the documented `dvr` object model "
    "instead, or enable the unrestricted tier if you genuinely need full Python."
)


def restricted_eval(expression: str, namespace: dict[str, Any]) -> Any:
    """Evaluate ``expression`` with no imports and no dunder access.

    ``namespace`` supplies the bound names (``r``, ``project``, ...). It is
    copied, so the caller's mapping is never mutated by the evaluation.
    """
    if "__" in expression:
        raise errors.DvrError(
            "Dunder attribute access is not allowed in restricted eval.",
            cause="Expressions containing `__` can escape to the interpreter.",
            fix=_ESCAPE_HINT,
            state={"expression": expression},
        )
    scope = dict(namespace)
    scope["__builtins__"] = SAFE_BUILTINS
    # Contained by SAFE_BUILTINS above: no imports, no dunder access.
    return eval(expression, scope)
