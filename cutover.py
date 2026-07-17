"""Cutover mechanism: shadow dual-write + rollback (foundation-blueprint §4 steps 5-6).

The strangler-fig: during the shadow period every write goes to BOTH the current store (source
of truth for reads) and the candidate store; reads compare on demand. When the candidate is
proven, flip which one reads serve. Rollback = flip back. Nothing is destroyed either way.

IMPORTANT: this is the AI Body's OWN memory cutover (toy ledger -> real BrainMemory). It does
NOT repoint the source memory store daemon; that flip is a deliberate, user-run act with its own backup.
"""
from __future__ import annotations

from ports import MemoryPort


class DualWriteMemory(MemoryPort):
    """Writes to primary AND shadow; reads from `read_from`. A shadow write that fails is logged
    (via on_divergence) and NEVER blocks the write to primary , the shadow is a candidate, not yet
    the source of truth. flip()/rollback() switch which store answers reads."""

    def __init__(self, primary: MemoryPort, shadow: MemoryPort, on_divergence=None) -> None:
        self.primary, self.shadow = primary, shadow
        self.read_from = primary
        self.on_divergence = on_divergence or (lambda kind, detail: None)
        self.shadow_write_failures = 0

    def _shadow(self, fn_name: str, *args) -> None:
        try:
            getattr(self.shadow, fn_name)(*args)
        except Exception as e:  # shadow is a candidate: its failure must not block the real write
            self.shadow_write_failures += 1
            self.on_divergence("shadow-write-failed", f"{fn_name}: {e}")

    def remember(self, text: str, scope: str) -> str:
        nid = self.primary.remember(text, scope)   # source of truth first
        self._shadow("remember", text, scope)
        return nid

    def supersede(self, old_id: str, new_text: str, actor: str, reason: str) -> str:
        nid = self.primary.supersede(old_id, new_text, actor, reason)
        self._shadow("supersede", old_id, new_text, actor, reason)
        return nid

    def invalidate(self, note_id: str, actor: str, reason: str) -> None:
        self.primary.invalidate(note_id, actor, reason)
        self._shadow("invalidate", note_id, actor, reason)

    def recall(self, query: str, k: int, scope: str = "global") -> list[dict]:
        return self.read_from.recall(query, k, scope)

    # --- shadow-period tools ---------------------------------------------------
    def compare_recall(self, query: str, k: int, scope: str = "global") -> dict:
        """Read from BOTH and report overlap , the shadow-period divergence check."""
        a = [h["id"] for h in self.primary.recall(query, k, scope)]
        b = [h["id"] for h in self.shadow.recall(query, k, scope)]
        overlap = len(set(a) & set(b)) / max(len(set(a) | set(b)), 1)
        if overlap < 1.0:
            self.on_divergence("read-divergence", f"query={query!r} overlap={overlap:.2f}")
        return {"primary": a, "shadow": b, "overlap": round(overlap, 3)}

    def flip(self) -> None:
        """Promote the shadow to serve reads (the cutover)."""
        self.read_from = self.shadow

    def rollback(self) -> None:
        """Point reads back at primary. Reversible at any moment; nothing is deleted."""
        self.read_from = self.primary
