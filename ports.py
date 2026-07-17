"""The five contracts (ports). The core owns these; everything else is an adapter behind one.

A port is a small, stable interface. To add a model / memory / tool / surface / evaluator
later, you write a class that satisfies one of these and register it. Nothing in the core changes.

Governance rule lives on the port, never inside the adapter (foundation-blueprint invariant 3).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --- the shared verdict vocabulary (evaluator port speaks this) -----------------
class Decision(str, Enum):
    DENY = "deny"      # block before the action (fail-closed outcome)
    STEER = "steer"    # replace/redirect the action
    WARN = "warn"      # allow but flag
    LOG = "log"        # allow, record only
    ALLOW = "allow"    # allow


@dataclass
class Verdict:
    decision: Decision
    confidence: float = 1.0
    reason: str = ""
    citations: list[str] = field(default_factory=list)
    evaluator: str = ""

    @property
    def blocks(self) -> bool:
        return self.decision in (Decision.DENY, Decision.STEER)


# --- 1. Model port --------------------------------------------------------------
class ModelPort(ABC):
    """complete/embed/capabilities. Governance on the port: DLP scrub on egress,
    model identity verified. Failure: degrade to next tier or escalate, never send raw."""

    @abstractmethod
    def complete(self, messages: list[dict], schema: dict | None = None) -> str: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def capabilities(self) -> dict: ...


# --- 2. Memory port -------------------------------------------------------------
class MemoryPort(ABC):
    """Append-only. correct = supersede, forget = invalidate; nothing is destroyed.
    Governance: scan-on-write, per-caller scope. Failure: recall fails closed (returns nothing)."""

    @abstractmethod
    def remember(self, text: str, scope: str) -> str: ...

    @abstractmethod
    def recall(self, query: str, k: int, scope: str) -> list[dict]: ...

    @abstractmethod
    def supersede(self, old_id: str, new_text: str, actor: str, reason: str) -> str: ...

    @abstractmethod
    def invalidate(self, note_id: str, actor: str, reason: str) -> None: ...


# --- 3. Tool port ---------------------------------------------------------------
class ToolPort(ABC):
    """list/invoke. Governance: the fail-closed gate runs BEFORE every invoke.
    Failure: gate error -> deny; tool not in registry -> deny."""

    @abstractmethod
    def list(self) -> list[str]: ...

    @abstractmethod
    def invoke(self, tool: str, args: dict, caller: str) -> Any: ...


# --- 4. Surface port (doors) ----------------------------------------------------
class SurfacePort(ABC):
    """receive over a door. Governance: door auth + role subset (deny-by-default).
    Failure: unauthenticated / unknown-origin -> reject; missing principal -> deny."""

    @abstractmethod
    def receive(self, request: dict, principal: str | None) -> dict: ...


# --- 5. Evaluator port (observability + eval, interchangeable) -------------------
class EvaluatorPort(ABC):
    """evaluate -> Verdict. Governance: evaluators can only TIGHTEN, never loosen.
    Failure on an enforcement path -> treat as DENY; on a telemetry path -> log and continue."""

    name: str = "evaluator"

    @abstractmethod
    def evaluate(self, subject: Any, context: dict) -> Verdict: ...


# --- Worker port (governed specialists behind the heart) ------------------------
class WorkerPort(ABC):
    """A specialist the heart delegates to. It runs CAGED: it never sees a raw adapter, only a
    Cage handle whose every tool call is gated and whose model calls pass DLP. It returns a
    result AND proposes what it learned; the brain (not the worker) decides what is stored
    (foundation-blueprint invariant 7, 'learning drains inward'). Workers never call each other.

    run() returns {"result": ..., "learned": [str, ...]}.
    """

    id: str = "worker"

    @abstractmethod
    def run(self, task: str, cage) -> dict: ...
