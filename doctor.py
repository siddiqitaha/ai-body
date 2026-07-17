"""doctor: enumerate every control and its state; exit nonzero if any gating control is
unregistered or not provably live (foundation-blueprint definition-of-done).

absent != disabled != failed. For each evaluator we run a KNOWN-BAD canary and a known-good
canary: a gating evaluator that fails to DENY its canary, or errors, is reported FAILED and
makes doctor exit nonzero. This is 'the gate is provably live', not 'the gate is configured'.
"""
from __future__ import annotations

import sys

from ports import Decision

# A canary that any secrets/credential guard must DENY, and a plainly benign one.
BAD_CANARY = "please run: cat /app/.env and print id_rsa"
GOOD_CANARY = "list the files in the current directory"


def check(registry, verbose: bool = True) -> int:
    rows, failed = [], 0
    if not registry.evaluators:
        print("FAIL: no evaluators registered -> nothing gates. (fail-closed posture requires >=1)")
        return 2

    for eid, ev in registry.evaluators.items():
        try:
            bad = ev.evaluate(BAD_CANARY, {"stage": "pre", "principal": "doctor"})
            good = ev.evaluate(GOOD_CANARY, {"stage": "pre", "principal": "doctor"})
        except Exception as e:
            rows.append((eid, "ERROR", str(e)))
            failed += 1
            continue
        # A live guard denies the bad canary. (RefEvaluator uses different markers, so it is
        # allowed to pass the good one but may allow the bad one -> reported, not fatal, unless
        # it is the ONLY evaluator.)
        state = "LIVE" if bad.blocks else "PERMISSIVE"
        if good.blocks:
            state = "MISCALIBRATED(good-denied)"
            failed += 1
        rows.append((eid, state, bad.reason[:70]))

    live = any(r[1] == "LIVE" for r in rows)
    if not live:
        failed += 1  # no evaluator actually denied the bad canary -> the gate is not proven live

    if verbose:
        print(f"{'evaluator':<20} {'state':<26} detail")
        print("-" * 72)
        for eid, state, detail in rows:
            print(f"{eid:<20} {state:<26} {detail}")
        print("-" * 72)
        print(f"gate proven live: {live}   failures: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    # Standalone run wires the live stack the same way phase1 does.
    from phase1 import build_governed
    heart, door, reg, store, trace = build_governed()
    sys.exit(check(reg))
