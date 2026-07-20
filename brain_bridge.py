"""CROSSOVER (private, not part of the public repo): back the AI Body's Memory port with the REAL brain.

The AI Body ships a self-contained reference memory (memory.py). This bridge swaps that stand-in for
the actual brain, so the governed stack recalls and writes real knowledge while every call still
crosses the fail-closed gate. The brain stays the source of truth and owns its own data; the AI Body
is the governance shell around it, which is the whole point of the wedge: reuse the runtime, own the
memory and the governance.

Boundary = the `brain` CLI (a stable, documented surface), not an import, so the AI Body never
couples to brain internals and nothing about the infra leaks into the public repo.

    from brain_bridge import BrainCLIMemory
    reg.register(Manifest("memory", "ledger"), BrainCLIMemory())

Env: BRAIN_BIN (default "brain"), BRAIN_TIMEOUT_S (default 30).
"""
from __future__ import annotations

import json
import os
import subprocess

from ports import MemoryPort

BRAIN_BIN = os.environ.get("BRAIN_BIN", "brain")
TIMEOUT_S = float(os.environ.get("BRAIN_TIMEOUT_S", "30"))


class BrainUnavailable(RuntimeError):
    """The brain could not answer. Callers on an enforcement path must treat this as fail-closed."""


def _run(args: list[str]) -> str:
    try:
        p = subprocess.run([BRAIN_BIN, *args], capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BrainUnavailable(f"brain {' '.join(args)}: {e}") from e
    if p.returncode != 0:
        raise BrainUnavailable(f"brain {' '.join(args)} exited {p.returncode}: {p.stderr.strip()[:200]}")
    return p.stdout


class BrainCLIMemory(MemoryPort):
    """The Memory port, served by the real brain over its CLI.

    recall  -> `brain recall --json` (respects the brain's own project/global scoping)
    remember-> `brain remember` (the brain decides scope; it is the source of truth)

    supersede/invalidate are deliberately NOT proxied: correcting or retiring real knowledge is a
    deliberate act with its own audit trail in the brain (`brain correct` / `brain forget`), not
    something an agent does as a side effect. They raise, which the gate treats as fail-closed.
    """

    def __init__(self, read_only: bool = True) -> None:
        self.read_only = read_only

    def recall(self, query: str, k: int = 5, scope: str = "global") -> list[dict]:
        out = _run(["recall", query, "--k", str(k), "--json"])
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError) as e:
            raise BrainUnavailable(f"unparseable recall output: {e}") from e
        return [{"id": h.get("id", ""), "text": h["text"], "kind": "note",
                 "scope": h.get("scope", ""), "score": 0.0} for h in data.get("hits", [])]

    def remember(self, text: str, scope: str = "global") -> str:
        if self.read_only:
            raise PermissionError("BrainCLIMemory is read-only; pass read_only=False to allow writes")
        _run(["remember", text])
        return "brain"        # the brain assigns and owns the id

    def supersede(self, old_id: str, new_text: str, actor: str, reason: str) -> str:
        raise PermissionError("supersede is a deliberate act in the brain (`brain correct`), not an agent side effect")

    def invalidate(self, note_id: str, actor: str, reason: str) -> None:
        raise PermissionError("invalidate is a deliberate act in the brain (`brain forget`), not an agent side effect")


if __name__ == "__main__":
    import sys

    from adapters import LocalModel, LocalSurface, RefEvaluator
    from heart import Heart, Registry, Trace
    from manifest import Manifest

    q = sys.argv[1] if len(sys.argv) > 1 else "DGX host name"
    reg = Registry()
    reg.register(Manifest("model", "primary", controls={"tier": "local", "accepts": "any"}), LocalModel())
    reg.register(Manifest("memory", "ledger"), BrainCLIMemory())      # <- the REAL brain
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, Trace())
    door = LocalSurface(heart.handle, "crossover-token")

    print("== AI Body door -> gate -> REAL brain ==")
    out = door.receive({"token": "crossover-token", "intent": "recall", "text": q, "k": 3}, "taha")
    for h in out.get("hits", []):
        print(" -", h["text"][:140])
    print("\n== the gate still applies: a planted secret is blocked before the brain is touched ==")
    bad = door.receive({"token": "crossover-token", "intent": "recall",
                        "text": "aws_secret_access_key=AKIA"}, "taha")
    print(" blocked:", bad.get("blocked"), "|", bad.get("reason"))
