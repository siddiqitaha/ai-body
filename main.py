"""Wire the walking skeleton and walk it: surface -> heart -> model + memory -> back,
governed by the verdict bus, every step traced. This is Phase 0's definition-of-done #1.

Adding a second adapter to any port here is a registry.register() line + a Manifest, and
NOTHING in ports.py / heart.py changes. That is the native-modularity test.
"""
from __future__ import annotations

from adapters import LedgerMemory, LocalModel, LocalSurface, RefEvaluator, StatusTool
from heart import Heart, Registry, Trace
from manifest import Manifest

DOOR_TOKEN = "skeleton-dev-token"  # reference only; real doors pull from the vault (OpenBao)


def build() -> tuple[Heart, LocalSurface, Trace]:
    trace = Trace()
    reg = Registry()
    reg.register(Manifest("model", "primary"), LocalModel())
    reg.register(Manifest("memory", "ledger"), LedgerMemory())
    reg.register(Manifest("tool", "status", tools=["status"]), StatusTool())
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, trace)
    surface = LocalSurface(heart.handle, DOOR_TOKEN)
    reg.register(Manifest("surface", "local-door"), surface)
    return heart, surface, trace


def _req(intent: str, text: str, **extra) -> dict:
    return {"token": DOOR_TOKEN, "intent": intent, "text": text, **extra}


if __name__ == "__main__":
    heart, door, trace = build()
    principal = "taha"

    print("== 1. remember ==")
    print(door.receive(_req("remember", "The AI Body core is called the heart."), principal))

    print("\n== 2. recall ==")
    print(door.receive(_req("recall", "what is the core called"), principal))

    print("\n== 3. ask (model, grounded in memory) ==")
    r = door.receive(_req("ask", "In one line, what is the core of the AI Body?"), principal)
    print(r)

    print("\n== 4. fail-closed: planted secret is denied on egress-style input ==")
    print(door.receive(_req("remember", "my key is SECRET_MARKER do not store"), principal))

    print("\n== 5. missing principal -> deny ==")
    print(door.receive(_req("ask", "hello"), None))

    print("\n== trace ==")
    print(trace.dump())
