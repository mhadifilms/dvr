#!/usr/bin/env python3
"""Parity-matrix consistency check (CI + local).

Validates the dvr↔pmr contract from dvr/schema.py:

1. Every parity entry has a valid status; pmr-only entries carry a reason
   (they are the gaps dvr must explain when raising ``NotSupportedError``).
   The reason may live on a sibling pmr-only entry in the same namespace
   (e.g. ``effects.apply`` is covered by ``effects.set_param``).
2. When the sibling pmr checkout is present (local dev), cross-check that
   both repos agree on shared operation names and statuses (a ``pmr-only``
   op here must not be ``dvr-only`` there, etc.).

Exit code 1 on any violation — wired into CI so agents extending either
repo are forced to keep the matrix truthful.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dvr.schema import PARITY

VALID_STATUSES = {"both", "dvr-only", "pmr-only"}


def load_pmr_parity() -> dict | None:
    """Import the sibling pmr repo's PARITY table, or None if unavailable."""
    pmr_repo = Path(__file__).resolve().parent.parent.parent / "pmr"
    if not (pmr_repo / "pmr" / "schema.py").exists():
        return None
    sys.path.insert(0, str(pmr_repo))
    try:
        module = importlib.import_module("pmr.schema")
    except Exception as exc:
        print(f"note: could not import pmr schema ({exc}); skipping cross-check")
        return None
    finally:
        sys.path.remove(str(pmr_repo))
    parity = getattr(module, "PARITY", None)
    return parity if isinstance(parity, dict) else None


def main() -> int:
    failures: list[str] = []

    reasoned_namespaces = {
        op.split(".")[0]
        for op, entry in PARITY.items()
        if entry.get("status") == "pmr-only" and entry.get("reason")
    }
    for op, entry in sorted(PARITY.items()):
        status = entry.get("status")
        if status not in VALID_STATUSES:
            failures.append(f"{op}: invalid status {status!r}")
        if (
            status == "pmr-only"
            and not entry.get("reason")
            and op.split(".")[0] not in reasoned_namespaces
        ):
            failures.append(f"{op}: pmr-only without a reason (in it or a namespace sibling)")

    pmr_parity = load_pmr_parity()
    if pmr_parity is not None:
        shared = set(PARITY) & set(pmr_parity)
        for op in sorted(shared):
            ours, theirs = PARITY[op].get("status"), pmr_parity[op].get("status")
            if ours != theirs:
                failures.append(f"{op}: status mismatch — dvr says {ours!r}, pmr says {theirs!r}")
        for op in sorted(set(PARITY) ^ set(pmr_parity)):
            missing_from = "pmr" if op in PARITY else "dvr"
            failures.append(f"{op}: missing from {missing_from}'s PARITY table")
        print(f"cross-checked {len(shared)} shared operations against pmr")

    if failures:
        print(f"\nPARITY CHECK FAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"parity check ok ({len(PARITY)} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
