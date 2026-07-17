"""The Acquire funnel: nothing lands ungoverned (foundation invariant 6).

Any new tool/model/MCP/skill passes: quarantine -> scan -> register-by-fingerprint -> sandbox
before it can be used. The fingerprint is the enforcement anchor: only the exact admitted digest
may run, and the digest is re-checked at INVOKE time, so a swap after admission is caught
(re-gate on change, invariant 5). Unadmitted or tampered -> deny (fail-closed, invariant 1).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ports import ToolPort


def fingerprint(spec: str) -> str:
    """The identity of a candidate = sha256 of its canonical spec (code / descriptor)."""
    return hashlib.sha256(spec.encode()).hexdigest()


@dataclass
class Admission:
    name: str
    digest: str
    sandbox: bool
    ok: bool
    reason: str


class AcquireFunnel:
    """Runs the intake stages. `evaluators` scan the candidate's spec the same way the verdict
    bus scans a live action , a candidate whose spec any guard denies is refused entry."""

    def __init__(self, evaluators: list) -> None:
        self.evaluators = evaluators
        self.admitted: dict[str, Admission] = {}

    def _scan(self, spec: str) -> tuple[bool, str]:
        for ev in self.evaluators:
            try:
                v = ev.evaluate(spec, {"stage": "acquire", "principal": "acquire"})
            except Exception as e:  # a scanner that errors on intake -> refuse (fail-closed)
                return False, f"scanner {getattr(ev,'name','?')} errored: {e}"
            if v.decision.value in ("deny", "steer"):
                return False, f"{getattr(ev,'name','?')} refused: {v.reason}"
        return True, "clean"

    def admit(self, name: str, spec: str, sandbox: bool = True) -> Admission:
        # 1. quarantine (implicit: nothing runs until this returns ok)
        # 2. scan
        clean, reason = self._scan(spec)
        if not clean:
            return Admission(name, "", sandbox, False, reason)
        # 3. register-by-fingerprint  4. mark sandbox
        digest = fingerprint(spec)
        adm = Admission(name, digest, sandbox, True, "admitted")
        self.admitted[name] = adm
        return adm

    def is_admitted(self, name: str, spec: str) -> bool:
        """True only if `name` was admitted AND its current spec still matches the admitted digest."""
        adm = self.admitted.get(name)
        return bool(adm and adm.ok and adm.digest == fingerprint(spec))


class GovernedTools(ToolPort):
    """A ToolPort that only invokes tools the funnel admitted, re-verifying the digest each call.
    Register a tool as (name, spec, fn); an unadmitted name, or a spec that no longer matches its
    admitted digest (tampered/swapped), is denied at invoke time."""

    def __init__(self, funnel: AcquireFunnel) -> None:
        self.funnel = funnel
        self._tools: dict[str, tuple[str, object]] = {}   # name -> (spec, fn)

    def register(self, name: str, spec: str, fn) -> None:
        self._tools[name] = (spec, fn)

    def list(self) -> list[str]:
        return [n for n, (spec, _) in self._tools.items() if self.funnel.is_admitted(n, spec)]

    def invoke(self, tool: str, args: dict, caller: str):
        entry = self._tools.get(tool)
        if entry is None:
            raise PermissionError(f"tool {tool!r} not registered")
        spec, fn = entry
        if not self.funnel.is_admitted(tool, spec):   # unadmitted or tampered -> deny
            raise PermissionError(f"tool {tool!r} not admitted (unscanned or digest changed)")
        return fn(args, caller)
