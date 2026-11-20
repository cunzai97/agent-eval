"""Scratch measurement harness (out of tree).

Modes:
  python3 .scratch/harness.py <snapshot> existing          # does the in-tree reference reproduce EXPECTED?
  python3 .scratch/harness.py <snapshot> run <casefile>    # measure + score cases in <casefile> against
                                                          # every previously-passing script in .scratch/ws/<task>
The module `evalkit.envs.shell_hidden` is imported read-only; candidate cases are injected into the
imported dicts in this process only, never written back.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import evalkit.envs.shell_hidden as sh

SNAPSHOT = {"script-002": ("script-dev-002", "freq.py"),
            "script-006": ("script-dev-006", "audit.py")}
REF = {"freq.py": ".scratch/ref/freq.py", "audit.py": ".scratch/ref/audit.py"}


def run_case(case: dict, script: Path, temp: Path) -> tuple[bool, str, dict]:
    """Materialise one case for `script` in `temp`, run every argv, return (ok, detail, outputs)."""
    shutil.copy2(script, temp / case["script"])
    for rel, content in case.get("files", {}).items():
        sh._write(temp / rel, content)
    detail = ""
    for argv in case.get("runs", [[]]):
        ok, out = sh._run([sys.executable, str(temp / case["script"]), *argv], temp)
        detail += out
        if not ok:
            return False, out[:400], {}
    outs = {}
    for rel in case["outs"]:
        p = temp / rel
        outs[rel] = sh._rows(p) if p.is_file() else None
    return True, detail[:400], outs


def ws_dirs(task: str) -> list[Path]:
    root = Path(".scratch/ws") / task
    return sorted(p for p in root.iterdir() if p.is_dir())


def main() -> int:
    snapshot = sys.argv[1]
    task, script_name = SNAPSHOT[snapshot]
    mode = sys.argv[2]
    ref_script = Path(REF[script_name]).resolve()

    if mode == "existing":
        cases = [cid for cid, _ in sh.CASES[snapshot]]
        dirs = ws_dirs(task)
        for cid in cases:
            case = dict(sh.FIXTURES[cid])
            with tempfile.TemporaryDirectory() as temp:
                ok, detail, outs = run_case(case, ref_script, Path(temp))
            want = sh.EXPECTED[cid]
            same = ok and outs == {k: want[k] for k in case["outs"]}
            print(f"[{'REF-OK ' if same else 'REF-BAD'}] {cid}")
            if not same:
                print("   want:", json.dumps(want, ensure_ascii=False)[:900])
                print("   got :", json.dumps(outs, ensure_ascii=False)[:900], detail[:300])
            # population sanity: every previously-passing script must still pass the committed case
            bad = []
            for d in dirs:
                script = d / script_name
                if not script.is_file():
                    continue
                with tempfile.TemporaryDirectory() as temp:
                    pok, pdetail, _ = run_case(case, script, Path(temp))
                if not pok:
                    bad.append(d.name)
            print(f"   population {len(dirs)-len(bad)}/{len(dirs)} pass  failing={bad}")
        return 0

    # mode == "run": candidate cases live in a python file exposing CANDIDATES: dict[str, dict]
    import importlib.util as iu
    spec = iu.spec_from_file_location("cand_cases", sys.argv[3])
    cc = iu.module_from_spec(spec)
    spec.loader.exec_module(cc)
    results = {}
    for cid, case in cc.CANDIDATES.items():
        with tempfile.TemporaryDirectory() as temp:
            ok, detail, outs = run_case(case, ref_script, Path(temp))
        if not ok:
            print(f"!! reference itself failed {cid}: {detail}")
            continue
        print(f"===== {cid}")
        print("MEASURED " + json.dumps({cid: outs}, ensure_ascii=False))
        kills, crashes, pass_n = [], [], 0
        for d in ws_dirs(task):
            script = d / script_name
            if not script.is_file():
                continue
            with tempfile.TemporaryDirectory() as temp:
                pok, pdetail, pouts = run_case(case, script, Path(temp))
            if not pok:
                crashes.append(d.name)
            elif pouts != outs:
                kills.append(d.name)
            else:
                pass_n += 1
        print(f"   agree {pass_n}  disagree {len(kills)}  exec-fail {len(crashes)}")
        results[cid] = {"measured": outs, "kill": kills + crashes}
    print("\nSUMMARY (union of killers)")
    allbad = set()
    for cid, r in results.items():
        print(f"  {cid:34s} kills {len(r['kill']):2d}  {r['kill']}")
        allbad |= set(r["kill"])
    print(f"  any-case-fails: {len(allbad)}  {sorted(allbad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
