"""Real parity harness: run the SAME self-retrieval proxy against the OLD brain and the NEW core,
same random sample, same seed. Answers the honest question the first proxy could not: is the new
core's 0.857 a REGRESSION vs the old data, or just what this corpus yields at k with this metric?

Optimisation: load every vector into memory ONCE per store and batch-embed the queries, so the
brute-force cosine is fast (the earlier ad-hoc check re-parsed all vectors on every query).

Read-only against both databases.
"""
from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import urllib.request

_BASE = os.path.dirname(os.path.abspath(__file__))
OLD_DB = os.environ.get("AIBODY_SOURCE_DB", os.path.join(_BASE, "source-memory.db"))
NEW_DB = os.path.join(_BASE, "brain-new.db")
EMBED_URL = "http://127.0.0.1:8001/v1/embeddings"
EMBED_MODEL = "Qwen/Qwen3-Embedding-4B"


def embed_batch(texts: list[str], bs: int = 32) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), bs):
        body = json.dumps({"model": EMBED_MODEL, "input": texts[i:i + bs]}).encode()
        req = urllib.request.Request(EMBED_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as r:
            out.extend(d["embedding"] for d in json.loads(r.read().decode())["data"])
    return out


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def load_store(db_path: str):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    notes = {r[0]: r[1] for r in db.execute(
        "SELECT id, text FROM notes WHERE status='active'").fetchall()}
    vecs = {}
    for nid, vec in db.execute("SELECT note_id, vec FROM note_vecs").fetchall():
        if nid in notes and nid not in vecs:
            vecs[nid] = _norm(json.loads(vec))
    return db, notes, vecs


def fts_ids(db, query: str, limit: int) -> list[int]:
    q = " OR ".join(t for t in query.replace('"', " ").split() if t) or query
    try:
        return [r[0] for r in db.execute(
            "SELECT n.id FROM notes_fts f JOIN notes n ON n.id=f.rowid "
            "WHERE notes_fts MATCH ? AND n.status='active' ORDER BY bm25(notes_fts) LIMIT ?",
            (q, limit)).fetchall()]
    except sqlite3.OperationalError:
        return []


def hybrid_hit(db, notes, vecs, nid_target: int, qvec, query: str, k: int, rrf_k: int = 60) -> bool:
    fts = fts_ids(db, query, k * 4)
    scored = sorted(((sum(a * b for a, b in zip(qvec, v)), nid) for nid, v in vecs.items()),
                    reverse=True)[:k * 4]
    vec = [nid for _, nid in scored]
    s: dict[int, float] = {}
    for rank, nid in enumerate(fts):
        s[nid] = s.get(nid, 0.0) + 1.0 / (rrf_k + rank)
    for rank, nid in enumerate(vec):
        s[nid] = s.get(nid, 0.0) + 1.0 / (rrf_k + rank)
    top = [nid for nid, _ in sorted(s.items(), key=lambda kv: kv[1], reverse=True)[:k]]
    return nid_target in top


def run_on(db, notes, vecs, ids: list[int], queries: list[str], qvecs, k: int = 12):
    hits = sum(hybrid_hit(db, notes, vecs, i, qv, q, k)
               for i, qv, q in zip(ids, qvecs, queries))
    return hits / len(ids) if ids else 0.0, len(ids)


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150

    # ids differ between stores (new core reassigned them), so match notes by TEXT.
    old_db, old_notes, old_vecs = load_store(OLD_DB)
    new_db, new_notes, new_vecs = load_store(NEW_DB)
    old_by_text = {t: i for i, t in old_notes.items() if i in old_vecs}
    new_by_text = {t: i for i, t in new_notes.items() if i in new_vecs}
    common_texts = sorted(set(old_by_text) & set(new_by_text))
    random.seed(7)
    texts = random.sample(common_texts, min(n, len(common_texts)))
    print(f"notes present (with vectors) in BOTH stores: {len(common_texts)}; "
          f"sampling {len(texts)} identical notes (seed 7)")

    queries = [" ".join(t.split()[:8]) for t in texts]
    qvecs = [_norm(v) for v in embed_batch(queries)]  # embed once, reuse for both stores

    old_rate, _ = run_on(old_db, old_notes, old_vecs, [old_by_text[t] for t in texts], queries, qvecs)
    new_rate, _ = run_on(new_db, new_notes, new_vecs, [new_by_text[t] for t in texts], queries, qvecs)
    print(f"OLD brain hit@12: {old_rate:.3f}")
    print(f"NEW core  hit@12: {new_rate:.3f}")
    delta = new_rate - old_rate
    print(f"delta (new - old): {delta:+.3f}  -> "
          f"{'PARITY: new matches/beats old' if delta >= -0.02 else 'REGRESSION: new is worse'}")
