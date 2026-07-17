"""Phase 3 tests: the memory core's governance + hybrid recall + the migration parity gate,
all without live services (the embedder is monkeypatched; the live run is migrate.py)."""
from __future__ import annotations

import memory as memmod
import migrate
from memory import BrainMemory


def _fake_embed(monkey_store):
    """Deterministic 8-dim bag-of-chars embedding so vector search is exercised offline."""
    def embed(texts, timeout_s=60.0):
        out = []
        for t in texts:
            v = [0.0] * 8
            for ch in t.lower():
                v[ord(ch) % 8] += 1.0
            out.append(v)
        return out
    return embed


def _mem(monkeypatch=None):
    memmod.embed = _fake_embed(None)  # patch module-level embed used by BrainMemory
    return BrainMemory(":memory:")


def test_scan_on_write_refuses_secret():
    m = _mem()
    try:
        m.remember("here is aws_secret_access_key=AKIA", "global")
        assert False, "must refuse to store a raw secret"
    except ValueError as e:
        assert "scan-on-write" in str(e)


def test_remember_and_hybrid_recall():
    m = _mem()
    m.remember("the heart is the deterministic coordinator of the ai body", "global")
    m.remember("qwen-heavy runs on port 8012 as the local model", "global")
    hits = m.recall("what coordinates the ai body", k=5, scope="global")
    assert any("heart" in h["text"] for h in hits)


def test_scope_isolation():
    m = _mem()
    m.remember("alice secret plan", "user:alice")
    assert m.recall("plan", k=5, scope="user:bob") == []


def test_supersede_hides_old_returns_new():
    m = _mem()
    a = m.remember("the model is gpt-oss-120b", "global")
    m.supersede(a, "the model is qwen-heavy 30b", "taha", "correction")
    hits = m.recall("which model", k=5, scope="global")
    texts = " ".join(h["text"] for h in hits)
    assert "qwen-heavy" in texts and "gpt-oss-120b" not in texts


def test_invalidate_removes():
    m = _mem()
    a = m.remember("temporary fact about the weather", "global")
    m.invalidate(a, "taha", "forget")
    assert m.recall("weather", k=5, scope="global") == []


def test_recall_degrades_to_fts_when_embedder_down():
    memmod.embed = lambda texts, timeout_s=60.0: None  # embedder down
    m = BrainMemory(":memory:")
    m.remember("the parity gate protects the migration", "global")
    hits = m.recall("parity gate migration", k=5, scope="global")
    assert any("parity" in h["text"] for h in hits)  # still returns via FTS alone


def test_parity_floor_matches_old_baseline():
    # the gate is 'new within CI of OLD'; the source memory store scores 0.913 on this proxy, so the floor
    # is the old baseline minus a small CI margin (~0.90), NOT an arbitrary absolute above it.
    assert 0.88 <= migrate.PARITY_FLOOR <= 0.92


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
