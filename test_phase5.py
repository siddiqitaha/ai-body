"""Phase 5 tests: the cutover mechanism (shadow dual-write + rollback) and the real-memory swap.
Offline: a fake embedder stands in for :8001."""
from __future__ import annotations

import memory as memmod
from cutover import DualWriteMemory
from memory import BrainMemory


def _mem():
    memmod.embed = lambda texts, timeout_s=60.0: None  # embedder down -> FTS-only, deterministic
    return BrainMemory(":memory:")


def test_dual_write_hits_both_stores():
    p, s = _mem(), _mem()
    dw = DualWriteMemory(p, s)
    dw.remember("the parity gate guards the cutover", "global")
    assert p.recall("parity gate", 5, "global") and s.recall("parity gate", 5, "global")


def test_reads_come_from_primary_until_flip():
    p, s = _mem(), _mem()
    dw = DualWriteMemory(p, s)
    dw.remember("shared note about ports", "global")
    assert dw.read_from is p
    dw.flip()
    assert dw.read_from is s
    dw.rollback()
    assert dw.read_from is p


def test_shadow_write_failure_never_blocks_primary():
    p = _mem()

    class BrokenShadow:
        def remember(self, *a): raise RuntimeError("shadow down")
        def supersede(self, *a): raise RuntimeError("down")
        def invalidate(self, *a): raise RuntimeError("down")
        def recall(self, *a): return []

    seen = []
    dw = DualWriteMemory(p, BrokenShadow(), on_divergence=lambda k, d: seen.append((k, d)))
    nid = dw.remember("must still land in primary", "global")  # must not raise
    assert nid and p.recall("must still land", 5, "global")
    assert dw.shadow_write_failures == 1 and seen and seen[0][0] == "shadow-write-failed"


def test_compare_recall_reports_overlap():
    p, s = _mem(), _mem()
    dw = DualWriteMemory(p, s)
    dw.remember("identical note in both stores", "global")
    cmp = dw.compare_recall("identical note", 5, "global")
    assert cmp["overlap"] == 1.0  # dual-written -> both return it


def test_rollback_after_flip_restores_primary_reads():
    p, s = _mem(), _mem()
    p.remember("only in primary", "global")
    dw = DualWriteMemory(p, s)
    dw.flip()
    assert dw.recall("only in primary", 5, "global") == []  # shadow doesn't have it
    dw.rollback()
    assert dw.recall("only in primary", 5, "global")         # primary does


def test_build_governed_accepts_real_memory_adapter():
    """The modularity test with a REAL adapter: swapping the Store port to BrainMemory is a
    one-line registry change, zero core edits."""
    memmod.embed = lambda texts, timeout_s=60.0: None
    import phase1
    heart, door, reg, store, trace = phase1.build_governed(real_memory=True)
    assert isinstance(reg.memories["ledger"], BrainMemory)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}"); passed += 1
        except Exception:
            print(f"FAIL {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
