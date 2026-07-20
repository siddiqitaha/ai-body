"""Phase 9 tests: the acquire funnel ARMED in the live stack (invariant 6, end to end).

Phase 7 proved the funnel in isolation. This proves the running heart's tool port is the
funnel-gated one: a caged worker can only reach a tool that was quarantined -> scanned ->
fingerprinted, a tampered tool is denied at invoke, a tool call whose args trip the DLP gate is
blocked before invoke, and adding a third tool is one admit+register (zero core edits)."""
from __future__ import annotations

from acquire import build_toolbox
from adapters import REPO_LS_SPEC, LedgerMemory, LocalModel, LocalSurface, RefEvaluator, ResearcherWorker, repo_ls
from heart import Heart, Registry, Trace
from manifest import Manifest
from ports import WorkerPort


def _stack():
    """The live wiring, but with a dead model base so the walk is deterministic + offline."""
    reg = Registry()
    reg.register(Manifest("model", "primary"), LocalModel(base="http://127.0.0.1:9/dead"))
    reg.register(Manifest("memory", "ledger"), LedgerMemory())
    box, funnel = build_toolbox([RefEvaluator()])
    reg.register(Manifest("tool", "toolbox", tools=["status", "repo_ls"]), box)
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, Trace())
    reg.register(Manifest("surface", "local-door"), LocalSurface(heart.handle, "t"))
    return heart, reg, box, funnel


def test_live_toolport_is_funnel_gated():
    _, _, box, _ = _stack()
    assert box.list() == ["status", "repo_ls", "repo_write"]   # all passed the funnel
    assert box.invoke("status", {}, "taha")["ok"]


def test_caged_worker_reaches_a_funnel_admitted_tool():
    heart, reg, _, _ = _stack()
    reg.register(Manifest("worker", "researcher", tools=["status"],
                          memory_scope="user:taha", callable_by=["heart"]), ResearcherWorker())
    r = heart.delegate("researcher", "what is the heart")
    # ok=True proves the worker reached the admitted `status` tool: an UNadmitted tool would raise
    # inside the cage and come back as a caged-stop (ok=False).
    assert r["ok"] is True and isinstance(r["result"], str)


def test_tampered_tool_denied_at_invoke_in_live_box():
    _, _, box, _ = _stack()
    box.register("repo_ls", REPO_LS_SPEC + " (swapped)", repo_ls)   # digest no longer matches admission
    try:
        box.invoke("repo_ls", {}, "taha")
        assert False, "tampered tool should be denied"
    except PermissionError:
        pass


def test_repo_ls_is_confined_to_the_repo_root():
    assert "adapters.py" in repo_ls({}, "taha")["entries"]          # sees the repo
    try:
        repo_ls({"sub": "../../etc"}, "taha")                       # escape attempt
        assert False, "path escape should be denied"
    except PermissionError:
        pass


def test_tool_args_pass_the_dlp_gate_through_the_cage():
    """A caged worker calling a tool with a secret marker in its args is blocked by the gate
    BEFORE the tool runs, the same DLP that guards a memory write guards a tool call."""
    heart, reg, _, _ = _stack()

    class Exfil(WorkerPort):
        id = "exfil"
        def run(self, task, cage):
            return {"leaked": cage.use_tool("repo_ls", {"sub": "aws_secret_access_key/x"})}

    reg.register(Manifest("worker", "exfil", tools=["repo_ls"],
                          memory_scope="user:taha", callable_by=["heart"]), Exfil())
    r = heart.delegate("exfil", "list things")
    assert r["ok"] is False and r.get("caged") and "DLP" in r["reason"]   # the cage blocked the tool call


def test_adding_a_third_tool_is_admit_plus_register_no_core_change():
    """Native modularity for the Tool port: a new tool is two lines on the SAME box object;
    heart.py and ports.py are never touched."""
    _, _, box, funnel = _stack()
    spec = "clock@v1: return a fixed marker; read-only"
    funnel.admit("clock", spec)
    box.register("clock", spec, lambda args, caller: {"tool": "clock", "ok": True})
    assert "clock" in box.list()
    assert box.invoke("clock", {}, "taha")["ok"]


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
    sys.exit(0)
