"""The manifest: what makes adding anything a config act, not a code change.

Every adapter/worker is declared as one small record. The heart reads it and wires the rest.
This is the native-modularity test: a second adapter on any port must be a manifest edit,
zero core code changes (foundation-blueprint definition-of-done).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Manifest:
    kind: str                                   # worker | model | tool | surface | evaluator
    id: str
    model: str = ""                             # which model row it uses (workers)
    tools: list[str] = field(default_factory=list)      # gate allowlist
    memory_scope: str = ""                      # which slice of memory it may read
    callable_by: list[str] = field(default_factory=lambda: ["heart"])  # master->worker edge
    controls: dict = field(default_factory=dict)        # governance knobs, e.g. {"autonomy": "L1"}
