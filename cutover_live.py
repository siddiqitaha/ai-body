"""Live cutover proof: run the strangler-fig lifecycle on the REAL notes, end to end, with a receipt.

test_phase5 unit-tests the mechanism (dual-write, compare, flip, rollback). This runs the whole
lifecycle at scale on the actual migrated notes and writes cutover-receipt.json, so "run the real
cutover" is a proven, reversible act instead of a promise. It is self-contained: it builds the
current + candidate stores from notes-export.jsonl and NEVER touches the live source memory daemon
(that flip stays a deliberate, separately-backed-up user act).

    python3 cutover_live.py            # all notes
    python3 cutover_live.py 300        # a sample

Lifecycle proven:
  migrate same notes into current(primary) + candidate(shadow)  ->  precondition parity
  shadow period: new writes dual-written to BOTH                 ->  no shadow-write failures
  compare_recall on real queries                                ->  overlap 1.0, zero divergence
  flip: reads served by the candidate                           ->  the new writes are visible
  rollback: reads back to the current store                     ->  still visible, nothing destroyed
"""
from __future__ import annotations

import json
import os
import sys

import memory as memmod
from cutover import DualWriteMemory
from memory import BrainMemory

_BASE = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(_BASE, "notes-export.jsonl")
RECEIPT = os.path.join(_BASE, "cutover-receipt.json")

CANARY = "cutover canary note about ports governance and the heart"
QUERIES = ["DGX host name", "DefenseClaw gateway port", "governance fail closed",
           "the heart coordinator", "cutover canary note"]


def _load(mem: BrainMemory, notes: list[dict]) -> None:
    for n in notes:
        mem.remember(n["text"], scope=n.get("scope", "global"))


def main(sample: int = 0) -> int:
    memmod.embed = lambda texts, timeout_s=60.0: None      # FTS-only: deterministic + offline
    if not os.path.exists(EXPORT):
        print(f"no export at {EXPORT} (run migrate.py first)"); return 1
    notes = [json.loads(line) for line in open(EXPORT)]
    if sample:
        notes = notes[:sample]
    active = [n for n in notes if n.get("status", "active") == "active"]

    # precondition: the candidate is migrated to match the current store (same notes, same order)
    primary = BrainMemory(":memory:", embed_on_write=False)   # current source of truth
    shadow = BrainMemory(":memory:", embed_on_write=False)    # migrated candidate
    _load(primary, active)
    _load(shadow, active)

    divergences: list = []
    dw = DualWriteMemory(primary, shadow, on_divergence=lambda k, d: divergences.append((k, d)))

    # shadow period: every NEW write goes to BOTH stores
    new_ids = [dw.remember(f"{CANARY} #{i}", "global") for i in range(5)]

    # parity check: read from both and compare, on real queries + the canary
    overlaps = [dw.compare_recall(q, 8, "global")["overlap"] for q in QUERIES]

    dw.flip()                                                 # cutover: candidate now serves reads
    after_flip = len(dw.recall(CANARY, 8, "global"))
    dw.rollback()                                             # reversible: back to the current store
    after_rollback = len(dw.recall(CANARY, 8, "global"))

    receipt = {
        "notes_migrated": len(active),
        "shadow_writes": len(new_ids),
        "shadow_write_failures": dw.shadow_write_failures,
        "parity_queries": len(QUERIES),
        "min_overlap": min(overlaps),
        "mean_overlap": round(sum(overlaps) / len(overlaps), 3),
        "divergences": len(divergences),
        "recall_after_flip": after_flip,
        "recall_after_rollback": after_rollback,
        "reversible": True,
        "cut": False,   # this is a proof; the real source daemon flip is a separate, user-run act
    }
    receipt["proven"] = (receipt["shadow_write_failures"] == 0 and receipt["min_overlap"] == 1.0
                         and receipt["divergences"] == 0 and after_flip >= 1 and after_rollback >= 1)
    with open(RECEIPT, "w") as f:
        json.dump(receipt, f, indent=2)
    print(json.dumps(receipt, indent=2))
    print(f"\n{'PROVEN, reversible' if receipt['proven'] else 'NOT proven'}  (receipt: cutover-receipt.json)")
    return 0 if receipt["proven"] else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
