"""Phase 13 tests: two real caged workers (researcher + coder), matching the running fleet and
tightening it. Per-worker tool allowlists are the fence; the coder's write is confined + gated +
fingerprint-admitted; delegation edges are deny-by-default so workers cannot call each other."""
from __future__ import annotations

import os
import shutil
import tempfile

from adapters import CoderWorker, LedgerMemory, LocalModel, RefEvaluator, ResearcherWorker
from heart import Heart, Registry, Trace
from manifest import Manifest


def _heart(scratch):
    os.environ["AIBODY_WRITE_ROOT"] = scratch          # confine the coder's writes to a temp sandbox
    from acquire import build_toolbox

    reg = Registry()
    reg.register(Manifest("model", "primary", controls={"tier": "local", "accepts": "any"}),
                 LocalModel(base="http://127.0.0.1:9/dead"))
    reg.register(Manifest("memory", "ledger"), LedgerMemory())
    box, _ = build_toolbox([RefEvaluator()])
    reg.register(Manifest("tool", "toolbox", tools=["status", "repo_ls", "repo_write"]), box)
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    reg.register(Manifest("worker", "researcher", tools=["status", "repo_ls"],
                          memory_scope="user:taha", callable_by=["heart"]), ResearcherWorker())
    reg.register(Manifest("worker", "coder", tools=["repo_ls", "repo_write"],
                          memory_scope="proj:coder", callable_by=["heart"]), CoderWorker())
    return Heart(reg, Trace()), reg


class _Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="aibody-coder-")
        return self.dir

    def __exit__(self, *a):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("AIBODY_WRITE_ROOT", None)


def test_coder_writes_a_governed_artifact():
    with _Sandbox() as scratch:
        heart, _ = _heart(scratch)
        r = heart.delegate("coder", "add a changelog entry")
        assert r["ok"] and r["result"]["wrote"]["bytes"] > 0
        assert os.path.isfile(os.path.join(scratch, "coder-notes.md"))   # it really wrote to the sandbox
        assert len(r["proposed"]) >= 1                                    # learning drains inward


def test_researcher_cannot_write():
    """The researcher's allowlist has no write tool: reaching for it is a caged-stop, not a crash."""
    with _Sandbox() as scratch:
        heart, _ = _heart(scratch)

        class Rogue(ResearcherWorker):
            def run(self, task, cage):
                return {"leaked": cage.use_tool("repo_write", {"path": "x", "content": "y"})}
        heart.reg.register(Manifest("worker", "researcher", tools=["status", "repo_ls"],
                                    memory_scope="user:taha", callable_by=["heart"]), Rogue())
        r = heart.delegate("researcher", "try to write")
        assert r["ok"] is False and r.get("caged") and "may not use tool" in r["reason"]


def test_coder_write_cannot_escape_the_sandbox():
    with _Sandbox() as scratch:
        heart, _ = _heart(scratch)
        from adapters import repo_write
        try:
            repo_write({"path": "../../etc/passwd", "content": "x"}, "coder")
            assert False, "path escape must be denied"
        except PermissionError:
            pass


def test_coder_write_with_a_secret_is_gated():
    """A secret marker in the write content is refused by the DLP gate before the file is touched."""
    with _Sandbox() as scratch:
        heart, _ = _heart(scratch)

        class Exfil(CoderWorker):
            def run(self, task, cage):
                return {"w": cage.use_tool("repo_write",
                                           {"path": "leak.txt", "content": "aws_secret_access_key=AKIA"})}
        heart.reg.register(Manifest("worker", "coder", tools=["repo_ls", "repo_write"],
                                    memory_scope="proj:coder", callable_by=["heart"]), Exfil())
        r = heart.delegate("coder", "leak")
        assert r["ok"] is False and r.get("caged") and "DLP" in r["reason"]
        assert not os.path.isfile(os.path.join(scratch, "leak.txt"))      # nothing was written


def test_delegation_is_deny_by_default_workers_cannot_call_each_other():
    with _Sandbox() as scratch:
        heart, _ = _heart(scratch)
        # only the heart is on each worker's callable_by edge; a worker as caller is refused
        assert heart.delegate("coder", "x", caller="researcher")["ok"] is False
        assert heart.delegate("researcher", "x", caller="coder")["ok"] is False
        assert heart.delegate("coder", "x", caller="heart")["ok"] is True


def test_both_workers_registered():
    with _Sandbox() as scratch:
        _, reg = _heart(scratch)
        assert set(reg.workers) == {"researcher", "coder"}


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
    sys.exit(0)
