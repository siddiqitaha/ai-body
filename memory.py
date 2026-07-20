"""The real memory core, behind MemoryPort. Append-only ledger + FTS + vector, fused by RRF.

This is the Phase 3 rebuild target. It is a NEW store under ~/ai-body; the source memory store
($AIBODY_SOURCE_DB) is only ever read, never touched. Migration (migrate.py) copies
the notes out, rebuilds the projections here, and a parity gate must pass before any cutover.

Governance on the port (foundation-blueprint 1.2): scan-on-write (secrets refused, not stored),
per-caller scope filter, append-only (correct=supersede, forget=invalidate). Failure: embedder
down -> recall degrades to FTS-only (still returns), write refuses rather than storing raw secret.
"""
from __future__ import annotations

import json
import math
import sqlite3
import urllib.request

from ports import MemoryPort

EMBED_URL = "http://127.0.0.1:8001/v1/embeddings"
EMBED_MODEL = "Qwen/Qwen3-Embedding-4B"
EMBED_DIM = 2560
SECRET_MARKERS = ("BEGIN RSA PRIVATE KEY", "aws_secret_access_key", "-----BEGIN", "SECRET_MARKER")


def embed(texts: list[str], timeout_s: float = 60.0) -> list[list[float]] | None:
    """Call the local embedder. Returns None on any failure (caller degrades to FTS-only)."""
    body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(EMBED_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            out = json.loads(r.read().decode())
        return [d["embedding"] for d in out["data"]]
    except Exception:
        return None


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class BrainMemory(MemoryPort):
    """Append-only notes + FTS5 + a vector table, hybrid-recalled with Reciprocal Rank Fusion.

    RRF = merge two ranked lists (keyword hits + nearest vectors) by summing 1/(k+rank); the
    consensus-winning fusion, no tuned weights. Below ~100k notes the vector search is exact
    brute force in-process (sqlite-vec territory), so no separate vector DB is needed here.
    """

    def __init__(self, path: str = ":memory:", embed_on_write: bool = True) -> None:
        # check_same_thread=False so a network door (HTTPSurface) can serve on a worker thread;
        # the reference HTTP server is single-threaded, so access to this connection stays serialized.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.embed_on_write = embed_on_write
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS notes(
              id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, kind TEXT DEFAULT 'note',
              scope TEXT NOT NULL DEFAULT 'global', status TEXT NOT NULL DEFAULT 'active',
              supersedes_id INTEGER, actor TEXT, reason TEXT, stated_at REAL);
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text, content='notes', content_rowid='id');
            CREATE TABLE IF NOT EXISTS note_vecs(note_id INTEGER PRIMARY KEY, dim INTEGER, vec TEXT);
        """)
        self.db.commit()

    # --- writes: scan-on-write, append-only ------------------------------------
    def _scan(self, text: str) -> None:
        for m in SECRET_MARKERS:
            if m.lower() in text.lower():
                raise ValueError(f"scan-on-write refused: secret marker {m!r} (tokenize first)")

    def _index(self, note_id: int, text: str) -> None:
        self.db.execute("INSERT INTO notes_fts(rowid, text) VALUES(?,?)", (note_id, text))
        if self.embed_on_write:
            vecs = embed([text])
            if vecs:
                self.db.execute("INSERT OR REPLACE INTO note_vecs(note_id, dim, vec) VALUES(?,?,?)",
                                (note_id, len(vecs[0]), json.dumps(vecs[0])))
        self.db.commit()

    def remember(self, text: str, scope: str, kind: str = "note", actor: str = "user") -> str:
        self._scan(text)
        cur = self.db.execute(
            "INSERT INTO notes(text, kind, scope, actor) VALUES(?,?,?,?)", (text, kind, scope, actor))
        self._index(cur.lastrowid, text)
        return str(cur.lastrowid)

    def supersede(self, old_id: str, new_text: str, actor: str, reason: str) -> str:
        self._scan(new_text)
        cur = self.db.execute(
            "INSERT INTO notes(text, kind, scope, supersedes_id, actor, reason) "
            "SELECT ?, kind, scope, id, ?, ? FROM notes WHERE id=?",
            (new_text, actor, reason, old_id))
        self.db.execute("UPDATE notes SET status='superseded' WHERE id=?", (old_id,))
        self._index(cur.lastrowid, new_text)
        return str(cur.lastrowid)

    def invalidate(self, note_id: str, actor: str, reason: str) -> None:
        self.db.execute("UPDATE notes SET status='invalidated', actor=?, reason=? WHERE id=?",
                        (actor, reason, note_id))
        self.db.commit()

    # --- recall: hybrid FTS + vector, fused by RRF -----------------------------
    def _fts_ids(self, query: str, scope: str, limit: int) -> list[int]:
        q = " OR ".join(t for t in query.replace('"', " ").split() if t) or query
        try:
            rows = self.db.execute(
                "SELECT n.id FROM notes_fts f JOIN notes n ON n.id=f.rowid "
                "WHERE notes_fts MATCH ? AND n.status='active' AND n.scope=? "
                "ORDER BY bm25(notes_fts) LIMIT ?", (q, scope, limit)).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            return []

    def _vec_ids(self, query: str, scope: str, limit: int) -> list[int]:
        qv = embed([query])
        if not qv:
            return []
        rows = self.db.execute(
            "SELECT v.note_id, v.vec FROM note_vecs v JOIN notes n ON n.id=v.note_id "
            "WHERE n.status='active' AND n.scope=?", (scope,)).fetchall()
        scored = sorted(((_cos(qv[0], json.loads(vec)), nid) for nid, vec in rows), reverse=True)
        return [nid for _, nid in scored[:limit]]

    def recall(self, query: str, k: int, scope: str = "global", rrf_k: int = 60) -> list[dict]:
        fts = self._fts_ids(query, scope, k * 4)
        vec = self._vec_ids(query, scope, k * 4)
        scores: dict[int, float] = {}
        for rank, nid in enumerate(fts):
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (rrf_k + rank)
        for rank, nid in enumerate(vec):
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (rrf_k + rank)
        top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        out = []
        for nid, score in top:
            row = self.db.execute("SELECT id, text, kind FROM notes WHERE id=?", (nid,)).fetchone()
            if row:
                out.append({"id": str(row[0]), "text": row[1], "kind": row[2], "score": round(score, 5)})
        return out

    def active_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM notes WHERE status='active'").fetchone()[0]
