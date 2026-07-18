# The AI Body

A governed local agent foundation: one personal AI system rebuilt as **five swappable ports
behind a deterministic heart**, where every model call, tool call, memory write, and answer
passes a **fail-closed gate** on the way through. Add anything later by writing one manifest ,
the core never changes.

Not a framework, not an LLM boss-agent. A small, owned coordinator that wires owned parts
together under one set of rules.

```
                request
                   v
                DOORS ............... token auth, deny-by-default
                   v
   +--------------------------------------------------+   the bus fires at:
   |  VERDICT BUS  (cross-cutting, fail-closed)       |    - the inbound request
   |  agent-control . local-scanner . guard-model .     |    - every tool call
   |  ref-dlp  ->  deny/steer/warn/log/allow          |    - model input + output
   |  worst wins . tighten-only . a guard error = deny|    - the answer (egress)
   +--------------------------------------------------+
                   v  (if allowed)
                THE HEART ........... registry . organ-graph . router . one door
                   v  gate each call
   PORTS   Model . Memory . Tool . Surface . [Evaluator = where guards plug in]
                   v  answer -> egress gate -> trace          (+ Worker, caged)
                OBSERVABILITY ....... eval store + traces -> Grafana  (fails open)
```

The verdict bus is **not** a downstream port , it is a band the flow passes through at the door,
at every model / tool / memory call (in and out), and on the answer. The Evaluator port is just
where guards plug in; the bus that calls them is cross-cutting.

### Two enforcement tiers (defense in depth)

The bus above is **tier 1**: fast, in-process, but it lives inside the agent , code that skips the
heart skips the bus. **Tier 2** is an out-of-process gateway (`gateway.py`) that owns the only
network route to the model and independently consults Agent Control before forwarding:

```
agent ─▶ [tier 1: heart bus, in-proc, all 4 guards] ─▶ [tier 2: LLM gateway, out-of-proc]
                                                              │ asks Agent Control :19381
                                                              ├ allow ─▶ model :8012
                                                              └ deny / AC down ─▶ 403, model never reached
```

Tier 1 is fast and catches most things; tier 2 is the choke point a compromised agent cannot
bypass. Live-proven: a benign prompt forwards to the model, a secrets-file prompt is blocked by
the gateway before the model is ever called. Point the Model adapter's base at the gateway to arm it.

Full visual: open [`docs/architecture.html`](docs/architecture.html) in a browser for the complete map.

---

## The seven rules no adapter may break

1. **Fail closed.** A gate that can't decide, or whose module is missing, denies.
2. **Own the data.** The memory ledger is ours, on-box, append-only. No external store is the source of truth.
3. **Govern on the port, not in the adapter.** Every call passes the gate at the contract boundary.
4. **Tighten-only automation.** Automatic actors may only make things stricter; loosening is a human act with a receipt.
5. **Re-gate on change.** A scheduled or looped action is re-checked at fire time against current calibration.
6. **Nothing lands ungoverned.** New models/tools/skills pass quarantine → scan → register-by-fingerprint → sandbox.
7. **Learning drains inward.** What a worker learns is proposed to the brain and stored in our ledger, or not at all.

---

## The five ports (contracts)

| Port | Contract | Reference adapter today | Governance on the port |
|---|---|---|---|
| **Model** | `complete` / `embed` / `capabilities` | `LocalModel` → qwen-heavy `:8012` | DLP scrub on egress; degrade, never send raw |
| **Memory** | `remember` / `recall` / `supersede` / `invalidate` | `BrainMemory` , notes + FTS + vectors, fused by RRF | scan-on-write, per-scope filter, append-only |
| **Tool** | `list` / `invoke` | `StatusTool` (one safe read-only tool) | fail-closed gate before every invoke; unknown → deny |
| **Surface** | `receive` | `LocalSurface` (token door) | door auth, role subset, missing principal → deny |
| **Evaluator** | `evaluate → Verdict{deny,steer,warn,log,allow}` | the verdict bus (four guards, below) | tighten-only; enforcement error → deny |
| *(Worker)* | `run(task, cage)` | `ResearcherWorker` (caged) | runs inside the cage; learning drains inward |

**The verdict bus** , four guards, each returns one of five decisions; the worst wins, evaluators
may only tighten, and any guard that errors on an enforcement path is treated as `deny`:

- `agent-control` , the live Agent Control server on `:19381` (fail-closed)
- `local-scanner` , the real LocalScanner scanner on `:18970` (fail-closed; needs `SCANNER_GATEWAY_TOKEN` to go live)
- `guard-model` , qwen-heavy judging SAFE/UNSAFE (**observe mode** until calibrated)
- `ref-dlp` , a deterministic secret-marker scan

---

## Run it

```bash
# one-command health check: all 8 definition-of-done boxes, exits nonzero if any fail
python3 accept.py

# the unit suites (44 tests, offline)
for t in test_skeleton test_phase1 test_phase2 test_phase3 test_phase4 test_phase5 test_phase6; do
  python3 $t.py; done

# the governed walk through the live Agent Control server (needs AC keys in env)
python3 phase1.py

# the memory rebuild + parity gate (read-only against the source memory store)
python3 migrate.py 0            # 0 = all notes; a number = sample that many
python3 parity_harness.py 150   # OLD brain vs NEW core, same sample
```

`accept.py` is the gate that says *is the foundation still proven?* , end-to-end walk, fail-closed
self-test, trace + eval store, memory parity, DLP block, the modularity test, a caged worker
delegation, and `doctor`. All green = proven.

---

## File map

| File | What it is |
|---|---|
| `ports.py` | the five contracts + the `Decision`/`Verdict` vocabulary + the Worker port |
| `heart.py` | registry, organ-graph, router, one door, the verdict bus, the `Cage`, `delegate` |
| `adapters.py` | one reference adapter per port + the four evaluators + the caged worker |
| `manifest.py` | the small declaration that makes adding anything a config act |
| `memory.py` | `BrainMemory` , append-only notes + FTS + vectors, hybrid recall (RRF) |
| `migrate.py` | memory migration with the parity gate (read-only toward the source memory store) |
| `parity_harness.py` | the honest old-vs-new recall comparison |
| `cutover.py` | shadow dual-write + rollback (the strangler-fig cutover mechanism) |
| `observ.py` | the eval store (verdicts) + OTLP trace export (fails open) |
| `doctor.py` | enumerates every guard, fails nonzero if none is provably live |
| `calibrate.py` | the promote-before-blocking gate (Se/Sp ≥ 0.90 on ≥ 50 labels) |
| `phase1.py` | wires the governed stack; `build_governed()` |
| `accept.py` | the one-command definition-of-done gate |
| `test_*.py` | 6 suites, 44 tests |

---

## Status

- **Foundation feature-complete.** All five ports have a real adapter; governed, monitored,
  with a caged-worker loop and a tested cutover mechanism.
- **Memory:** 1930 notes migrated into a side copy; recall parity with the source memory store confirmed
  (hit@12 0.913 = 0.913, delta 0.000). No cutover performed , the source memory store is untouched.
- **Tests:** 44/44 unit + `accept.py` 8/8 green.

### Waiting on a human (each a single step)

- Label 50-100 real cases → `calibrate.py` promotes the guard model from observe to blocking.
- Provide `SCANNER_GATEWAY_TOKEN` → the LocalScanner guard goes live.
- Run the real live-brain cutover when chosen → the mechanism is proven and reversible.

From here the AI Body grows by adding the next worker, tool, model, or surface , one adapter at a time.
