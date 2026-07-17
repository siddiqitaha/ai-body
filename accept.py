"""The foundation acceptance gate: runs every box of the walking-skeleton definition-of-done
(foundation-blueprint §5) end to end and prints a green/red board. Exit nonzero if any box fails.

This is the one command that answers 'is the foundation still proven?'. It uses the REAL memory
core and the REAL local model; the guard boxes use offline evaluators so the gate runs without
the Agent Control keys (the live-AC path is proven separately by phase1.py).
"""
from __future__ import annotations

import random
import sqlite3
import sys

import memory as memmod
from adapters import LocalModel, RefEvaluator, ResearcherWorker, StatusTool
from cutover import DualWriteMemory
from doctor import check as doctor_check
from heart import Heart, Registry, Trace
from manifest import Manifest
from memory import BrainMemory
from observ import EvalStore
from ports import Decision, EvaluatorPort, Verdict

NEW_DB = "./brain-new.db"
results: list[tuple[str, bool, str]] = []


def box(name):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"exception: {e}"
        results.append((name, ok, detail))
    return deco


def _stack(real_model=False):
    reg, trace, store = Registry(), Trace(), EvalStore()
    reg.register(Manifest("model", "primary"), LocalModel() if real_model else _Echo())
    reg.register(Manifest("memory", "ledger"), BrainMemory(":memory:"))
    reg.register(Manifest("tool", "status", tools=["status"]), StatusTool())
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, trace, eval_store=store)
    return heart, reg, trace, store


class _Echo(LocalModel):
    def complete(self, messages, schema=None):
        return "ok: " + next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")[:40]


@box("1. end-to-end walk (surface->heart->model->memory->back), REAL model")
def _b1():
    memmod.embed = lambda t, timeout_s=60.0: None
    heart, reg, _, _ = _stack(real_model=True)
    heart.reg.memories["ledger"].remember("the heart coordinates the ai body", "user:taha")
    r = heart.handle({"intent": "ask", "text": "what coordinates the ai body"}, "taha")
    return r.get("ok", False) and bool(r.get("answer")), f"answer len {len(r.get('answer',''))}"


@box("2. fail-closed self-test (broken gate -> action DENIED, not allowed)")
def _b2():
    memmod.embed = lambda t, timeout_s=60.0: None
    heart, reg, _, _ = _stack()

    class Broken(EvaluatorPort):
        name = "broken"
        def evaluate(self, s, c): raise RuntimeError("gate down")

    reg.register(Manifest("evaluator", "broken"), Broken())
    r = heart.handle({"intent": "ask", "text": "anything"}, "taha")
    return r.get("blocked") and r["decision"] == "deny", str(r.get("reason", ""))[:50]


@box("3. every step traced + eval store records verdicts")
def _b3():
    memmod.embed = lambda t, timeout_s=60.0: None
    heart, reg, trace, store = _stack()
    heart.handle({"intent": "ask", "text": "hello"}, "taha")
    return len(trace.spans) > 0 and sum(store.counts().values()) > 0, \
        f"{len(trace.spans)} spans, {sum(store.counts().values())} verdicts"


@box("4. memory migration parity: NEW core within CI of OLD brain (same sample)")
def _b4():
    # Run the real old-vs-new harness in a CLEAN subprocess (no monkeypatch contamination).
    # The gate is 'new >= old - 0.02', the correct metric, not an absolute floor.
    import subprocess
    out = subprocess.run(
        [sys.executable, "./parity_harness.py", "40"],
        capture_output=True, text=True, timeout=170, cwd=".").stdout
    old = new = None
    for line in out.splitlines():
        if "OLD brain hit@12" in line:
            old = float(line.split(":")[1])
        if "NEW core  hit@12" in line:
            new = float(line.split(":")[1])
    if old is None or new is None:
        return False, f"harness output unparsed: {out[-80:]!r}"
    return new >= old - 0.02, f"new {new:.3f} vs old {old:.3f} (delta {new-old:+.3f})"


@box("5. DLP self-test (planted secret blocked on write, not stored)")
def _b5():
    memmod.embed = lambda t, timeout_s=60.0: None
    heart, reg, _, _ = _stack()
    r = heart.handle({"intent": "remember", "text": "aws_secret_access_key=AKIA123"}, "taha")
    return r.get("blocked") and r["decision"] == "deny", str(r.get("reason", ""))[:50]


@box("6. modularity test (2nd model adapter = one register(), zero core edits)")
def _b6():
    memmod.embed = lambda t, timeout_s=60.0: None
    heart, reg, _, _ = _stack()
    before = set(reg.models)
    reg.register(Manifest("model", "secondary"), _Echo())
    return set(reg.models) - before == {"secondary"}, "added 'secondary'"


@box("7. worker delegation runs CAGED end to end (delegate->gate->return->propose->store)")
def _b7():
    memmod.embed = lambda t, timeout_s=60.0: None
    heart, reg, _, _ = _stack()
    reg.register(Manifest("worker", "researcher", tools=["status"],
                          memory_scope="user:taha", callable_by=["heart"]), ResearcherWorker())
    r = heart.delegate("researcher", "what is the heart")
    return r.get("ok") and len(r.get("stored", [])) >= 1, \
        f"proposed {len(r.get('proposed',[]))}, stored {len(r.get('stored',[]))}"


@box("8. doctor enumerates controls + fails nonzero if no gate is provably live")
def _b8():
    memmod.embed = lambda t, timeout_s=60.0: None
    _, reg, _, _ = _stack()
    # add a live gate (denies the bad canary) so doctor should PASS (exit 0)
    class Gate(EvaluatorPort):
        name = "gate"
        def evaluate(self, s, c):
            bad = any(m in str(s).lower() for m in (".env", "id_rsa", ".ssh/"))
            return Verdict(Decision.DENY if bad else Decision.ALLOW, reason="canary")
    reg.register(Manifest("evaluator", "gate"), Gate())
    rc = doctor_check(reg, verbose=False)
    return rc == 0, f"doctor exit {rc}"


if __name__ == "__main__":
    for fn in [_b1, _b2, _b3, _b4, _b5, _b6, _b7, _b8]:
        pass  # boxes already ran at import via the decorator
    print("\n  AI BODY FOUNDATION , DEFINITION OF DONE\n" + "  " + "-" * 60)
    allok = True
    for name, ok, detail in results:
        allok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")
    print("  " + "-" * 60)
    print(f"  {'ALL GREEN , foundation proven' if allok else 'RED , foundation NOT proven'}")
    sys.exit(0 if allok else 1)
