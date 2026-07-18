"""The heart: the coordinator we never rent. Deterministic, NOT an LLM boss-agent.

Four jobs (foundation-blueprint §2):
  1. Registry     - the live list of every adapter behind every port, each with its manifest.
  2. Organ-graph  - who may call whom (declared, not emergent).
  3. Orchestrator - route a request: answer from memory, reason locally, or (later) delegate.
  4. One door     - a single governed entry: auth + gate + trace happen in exactly one place.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from manifest import Manifest
from ports import (
    Decision,
    EvaluatorPort,
    MemoryPort,
    ModelPort,
    SurfacePort,
    ToolPort,
    Verdict,
)


# --- trace: every step is visible (foundation-blueprint: monitoring from line one) ---
@dataclass
class Trace:
    spans: list[dict] = field(default_factory=list)

    def span(self, name: str, **attrs) -> None:
        # No wall-clock ordering assumptions; monotonic ns is fine and always available.
        self.spans.append({"t": time.monotonic_ns(), "name": name, **attrs})

    def dump(self) -> str:
        return "\n".join(f"  [{s['name']}] " + " ".join(
            f"{k}={v}" for k, v in s.items() if k not in ("t", "name")) for s in self.spans)


class Registry:
    """The live list of adapters behind each port + their manifests + the organ-graph."""

    def __init__(self) -> None:
        self.models: dict[str, ModelPort] = {}
        self.memories: dict[str, MemoryPort] = {}
        self.tools: dict[str, ToolPort] = {}
        self.surfaces: dict[str, SurfacePort] = {}
        self.evaluators: dict[str, EvaluatorPort] = {}
        self.workers: dict[str, object] = {}
        self.manifests: dict[str, Manifest] = {}

    def register(self, manifest: Manifest, adapter) -> None:
        table = {
            "model": self.models, "memory": self.memories, "tool": self.tools,
            "surface": self.surfaces, "evaluator": self.evaluators, "worker": self.workers,
        }.get(manifest.kind)
        if table is None:
            raise ValueError(f"unknown port kind {manifest.kind!r}")
        table[manifest.id] = adapter
        self.manifests[manifest.id] = manifest

    def may_call(self, caller: str, target: str) -> bool:
        """Organ-graph edge check: declared, deny-by-default."""
        m = self.manifests.get(target)
        if m is None:
            return False
        return caller in m.callable_by


class Cage:
    """The restricted handle a worker runs inside. It NEVER holds a raw adapter. Every tool
    call is checked against the worker's manifest allowlist AND the fail-closed gate; every
    model call passes the same egress gate. A worker cannot reach a tool not in its manifest,
    nor bypass the bus. This is the in-process half of the boundary; a real untrusted worker
    also gets an OS sandbox + host firewall (the hard boundary), noted, not built in Phase 4.
    """

    def __init__(self, heart: "Heart", worker_id: str, allowed_tools: list[str],
                 memory_scope: str) -> None:
        self._heart, self.worker_id = heart, worker_id
        self._allowed = set(allowed_tools)
        self._scope = memory_scope
        self.learned: list[str] = []

    def use_tool(self, tool: str, args: dict) -> Any:
        h = self._heart
        if tool not in self._allowed:  # not in the worker's manifest allowlist -> deny
            h.trace.span("cage.tool-denied", worker=self.worker_id, tool=tool, reason="not in allowlist")
            raise PermissionError(f"worker {self.worker_id!r} may not use tool {tool!r}")
        v = h._gate({"tool": tool, "args": args}, {"stage": "pre", "principal": self.worker_id})
        if v.blocks:
            h.trace.span("cage.tool-blocked", worker=self.worker_id, tool=tool, reason=v.reason)
            raise PermissionError(f"gate blocked tool {tool!r}: {v.reason}")
        # resolve the tool adapter that owns this tool and invoke through it
        for tid, adapter in h.reg.tools.items():
            if tool in adapter.list():
                h.trace.span("cage.tool", worker=self.worker_id, tool=tool)
                return adapter.invoke(tool, args, caller=self.worker_id)
        raise PermissionError(f"tool {tool!r} not registered")  # unknown tool -> deny

    def think(self, prompt: str) -> str:
        h = self._heart
        v = h._gate(prompt, {"stage": "pre", "principal": self.worker_id})
        if v.blocks:
            raise PermissionError(f"gate blocked model input: {v.reason}")
        model = h.reg.models.get("primary")
        answer = model.complete([{"role": "user", "content": prompt}])
        vo = h._gate(answer, {"stage": "post", "principal": self.worker_id})  # egress gate
        if vo.blocks:
            raise PermissionError(f"gate blocked model output: {vo.reason}")
        h.trace.span("cage.think", worker=self.worker_id, chars=len(answer))
        return answer

    def recall(self, query: str, k: int = 3) -> list[dict]:
        # a worker sees only its own memory slice
        return self._heart.reg.memories["ledger"].recall(query, k=k, scope=self._scope)

    def propose(self, learning: str) -> None:
        """The worker proposes a fact; the heart (brain) decides if it is kept. Never stored here."""
        self.learned.append(learning)


class Heart:
    """Registry + organ-graph + router + one door + the verdict bus (fail-closed)."""

    def __init__(self, registry: Registry, trace: Trace | None = None, eval_store=None) -> None:
        self.reg = registry
        self.trace = trace or Trace()
        self.eval_store = eval_store  # optional: records every verdict (observ.EvalStore)

    # --- the verdict bus: run enforcement evaluators, fail CLOSED -----------------
    def _gate(self, subject, context: dict) -> Verdict:
        """Ask every registered evaluator; tighten-only; any error on this path = DENY."""
        worst = Verdict(Decision.ALLOW, reason="no evaluator objected")
        order = [Decision.ALLOW, Decision.LOG, Decision.WARN, Decision.STEER, Decision.DENY]
        for eid, ev in self.reg.evaluators.items():
            try:
                v = ev.evaluate(subject, context)
                v.evaluator = eid
            except Exception as e:  # a broken evaluator on an enforcement path denies
                self.trace.span("evaluator-error", evaluator=eid, error=str(e), effect="deny")
                denied = Verdict(Decision.DENY, reason=f"evaluator {eid} failed: {e}", evaluator=eid)
                self._record(denied, subject, context)
                return denied
            self.trace.span("evaluate", evaluator=eid, decision=v.decision.value,
                            confidence=v.confidence)
            self._record(v, subject, context)
            if order.index(v.decision) > order.index(worst.decision):  # tighten-only
                worst = v
        return worst

    # --- delegation: the caged worker loop (foundation-blueprint §3) --------------
    def delegate(self, worker_id: str, task: str, caller: str = "heart") -> dict:
        self.trace.span("delegate", worker=worker_id, caller=caller)
        if not self.reg.may_call(caller, worker_id):  # organ-graph edge, deny-by-default
            self.trace.span("delegate.denied", worker=worker_id, reason="no organ-graph edge")
            return {"ok": False, "error": f"{caller!r} may not call {worker_id!r}"}
        worker = self.reg.workers.get(worker_id)
        manifest = self.reg.manifests.get(worker_id)
        if worker is None or manifest is None:
            return {"ok": False, "error": f"worker {worker_id!r} not registered"}

        # gate the task itself before the worker ever sees it
        v = self._gate(task, {"stage": "pre", "principal": worker_id})
        if v.blocks:
            self.trace.span("delegate.task-blocked", worker=worker_id, reason=v.reason)
            return {"ok": False, "blocked": True, "decision": v.decision.value, "reason": v.reason}

        cage = Cage(self, worker_id, manifest.tools, manifest.memory_scope or "global")
        try:
            out = worker.run(task, cage)
        except PermissionError as e:  # the cage denied something -> the worker is stopped, not the box
            self.trace.span("delegate.caged-stop", worker=worker_id, reason=str(e))
            return {"ok": False, "caged": True, "reason": str(e), "proposed": cage.learned}

        # learning drains inward: the brain decides what (if anything) is stored, scan-on-write
        stored = self._accept_learnings(cage.learned, worker_id, manifest.memory_scope or "global")
        self.trace.span("delegate.done", worker=worker_id, proposed=len(cage.learned), stored=len(stored))
        return {"ok": True, "result": out.get("result"),
                "proposed": cage.learned, "stored": stored}

    def _accept_learnings(self, learnings: list[str], worker_id: str, scope: str) -> list[str]:
        mem = self.reg.memories.get("ledger")
        stored = []
        for fact in learnings:
            try:  # scan-on-write lives in the ledger; a secret-bearing "learning" is refused
                nid = mem.remember(fact, scope=scope)
                stored.append(nid)
            except Exception as e:
                self.trace.span("learning.refused", worker=worker_id, reason=str(e))
        return stored

    def _record(self, v: Verdict, subject, context: dict) -> None:
        if self.eval_store is None:
            return
        self.eval_store.record(
            stage=context.get("stage", "?"), principal=context.get("principal", "?"),
            evaluator=v.evaluator or "?", decision=v.decision.value,
            confidence=v.confidence, reason=v.reason, subject=subject)

    # --- the router: cheap-first (foundation-blueprint §2.3) ---------------------
    def handle(self, request: dict, principal: str | None) -> dict:
        self.trace.span("door.receive", principal=principal or "<none>",
                        intent=request.get("intent", "?"))
        if principal is None:  # missing principal -> deny, never default to admin
            self.trace.span("door.deny", reason="missing principal")
            return {"ok": False, "error": "unauthenticated"}

        intent = request.get("intent")
        text = request.get("text", "")

        # gate the inbound request itself before doing anything with it
        v = self._gate(text, {"stage": "pre", "intent": intent, "principal": principal})
        if v.blocks:
            self.trace.span("router.blocked", decision=v.decision.value, reason=v.reason)
            return {"ok": False, "blocked": True, "decision": v.decision.value, "reason": v.reason}

        if intent == "remember":
            mem = self.reg.memories.get("ledger")
            nid = mem.remember(text, scope=request.get("scope", f"user:{principal}"))
            self.trace.span("memory.remember", id=nid)
            return {"ok": True, "id": nid}

        if intent == "recall":
            mem = self.reg.memories.get("ledger")
            hits = mem.recall(text, k=request.get("k", 5), scope=request.get("scope", f"user:{principal}"))
            self.trace.span("memory.recall", hits=len(hits))
            return {"ok": True, "hits": hits}

        # default: answer with the model, grounded in a memory recall (the end-to-end walk)
        mem = self.reg.memories.get("ledger")
        model = self.reg.models.get("primary")
        context_notes = mem.recall(text, k=3, scope=f"user:{principal}")
        self.trace.span("memory.recall", hits=len(context_notes))
        msgs = [
            {"role": "system", "content": "You are the AI Body core. Answer briefly."},
            {"role": "user", "content": text
             + ("\n\nKnown notes:\n" + "\n".join(n["text"] for n in context_notes)
                if context_notes else "")},
        ]
        answer = model.complete(msgs)
        self.trace.span("model.complete", chars=len(answer))
        # gate the outbound answer (egress) before returning it
        vo = self._gate(answer, {"stage": "post", "intent": intent, "principal": principal})
        if vo.blocks:
            self.trace.span("router.egress-blocked", decision=vo.decision.value, reason=vo.reason)
            return {"ok": False, "blocked": True, "decision": vo.decision.value, "reason": vo.reason}
        return {"ok": True, "answer": answer, "grounded_in": len(context_notes)}
