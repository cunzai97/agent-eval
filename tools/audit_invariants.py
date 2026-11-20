"""两条不变量的取证闸门：每题必须"仅凭题面可解"且"可重复测试"。

    python3 tools/audit_invariants.py                 # 全 50 题
    python3 tools/audit_invariants.py --tasks debug-stacktrace-001 --jobs 1
    python3 tools/audit_invariants.py --json .scratch/invariants.json

对每题做四件事，全部可复核：

1. **可解**：把历史上判对过的轨迹（按步数从少到多，最多 --cap 条）灌进**当前**闸门重跑
   `run_checks`；第一条全项通过即作为证据留下。没有历史通过记录的题（新题）标 NO_HISTORY，
   需要 docs/hardening_evidence/ 里的参考产物来证明，不能算已保证。
2. **可重复**：把那条证据轨迹**再跑一次**，比对判定与终态。任何差异都是 nondeterminism，
   并打印具体差在哪个 key。
3. **静态隐患**：扫快照与隐藏测试里的未播种 random、墙上时钟、本地时区、Unicode 数据库
   版本、网络与睡眠依赖。
4. **预算余量**：历史通过轨迹的最大 steps / delta tokens / elapsed_s 对比题面 budget，
   余量不足会让"超时"伪装成"能力不足"。

退出码：有任何题不成立即 1，可直接进 CI 或每次改题后手动跑。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml                                                  # noqa: E402
from evalkit.schema import TaskSpec, Trajectory             # noqa: E402
from evalkit.verifier import run_checks                      # noqa: E402
from evalkit.envs import get_env                             # noqa: E402
from evalkit.fingerprint import task_fingerprint             # noqa: E402
from evalkit.envs.base import ToolResult                     # noqa: E402

HAZARDS = {
    "wall-clock": re.compile(r"\bdatetime\.now\(|\btime\.time\(|date\.today\(|\butcnow\("),
    "local-tz": re.compile(r"\blocaltime\(|\bastimezone\(\s*\)|TZ=|[Aa]merica/"),
    # `unicodedata` 不是时区依赖，别塞进 local-tz 里冒充它：casefold/NFKD 的结果随 CPython
    # 自带的 Unicode 数据库版本而变。隐藏用例跑在按 digest 钉死的 python/pytest 镜像里，
    # 所以这是**已被钉住**的确定性来源；单列一项只是提醒——换镜像 digest 前要复跑定标。
    "unicode-db-version": re.compile(r"\bunicodedata\."),
    "unseeded-random": re.compile(r"\brandom\.(randint|choice|shuffle|sample|random|uniform|randrange)\("),
    "sleep": re.compile(r"\btime\.sleep\("),
    "network": re.compile(r"\burllib\.request|\bsocket\.|\brequests\.|\bcurl \b|\bwget \b"),
}


def load_specs():
    out = {}
    for f in sorted(glob.glob(str(ROOT / "tasks/*/*.yaml"))):
        d = yaml.safe_load(open(f))
        out[d["id"]] = (TaskSpec(**d), os.path.relpath(f, ROOT))
    return out


def replay(spec: TaskSpec, tr: dict):
    """在当前闸门下重建环境并求值，返回 (checks 判定, 终态)。"""
    with tempfile.TemporaryDirectory(prefix="audit-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        for step in tr.get("steps", []):
            for tc in step.get("tool_calls", []):
                env.call_tool(tc.get("name"), tc.get("args") or {})
        state = env.state()
    checks = run_checks(spec, Trajectory(**tr), state)
    return checks, state


def state_digest(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:16]


TIMING_FIELDS = re.compile(r"\b(summary|elapsed_s|latency_ms|duration|now|today)\b")


def timing_dependent_asserts(spec: TaskSpec) -> list[str]:
    """断言里读到了带计时的字段 = 判定会随机器快慢翻，属于可重复性缺陷。"""
    bad = []
    for c in spec.success:
        if c.type != "state_assert":
            continue
        for e in ([c.expr] if c.expr else (c.all_of or [])):
            if e and TIMING_FIELDS.search(e):
                bad.append(e[:90])
    return bad


def reference_path(task_id: str) -> Path:
    return ROOT / "docs/hardening_evidence/references" / f"{task_id}.json"


def replay_calls(spec: TaskSpec, calls: list) -> tuple[bool, dict, dict]:
    """把一份"参考动作序列"当成模型的正确解灌进环境，返回 (判定, 终态, 合成轨迹)。

    格式：{"note": str, "calls": [[tool_name, {args}], ...]}，只准用题面声明的工具。
    这是不依赖任何模型、只用题面信息证明"可解"的那半边证据。
    """
    steps = []
    failed_calls = []
    with tempfile.TemporaryDirectory(prefix="audit-ref-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        declared = set(spec.agent.tools)
        for i, (name, args) in enumerate(calls, 1):
            if name not in declared:
                raise ValueError(f"reference uses undeclared tool {name!r} (task declares {sorted(declared)})")
            if name == "done":
                # done 由 agent_loop 注入，不在 env.tools() 里；参考解以它收尾是正常的
                res = ToolResult(True, {"done": True})
            else:
                res = env.call_tool(name, args or {})
            if not res.ok:
                failed_calls.append(f"#{i} {name}: {str(res.error)[:90]}")
            steps.append({"idx": i, "thought": "", "tool_calls": [{"id": f"r{i}", "name": name, "args": args or {}}],
                          "observations": [res.to_obs(name)], "tokens": {"input": 0, "output": 0},
                          "latency_ms": 0})
        state = env.state()
    traj = {"task_id": spec.id, "model": "reference", "seed": 0, "started_at": "1970-01-01T00:00:00Z",
            "steps": steps, "termination": "done", "total_steps": len(steps), "total_tokens": 0,
            "elapsed_s": 0.0, "error": None}
    checks = run_checks(spec, Trajectory(**traj), state)
    return all(c.passed for c in checks) if checks else False, state, {
        "checks": [[c.detail, bool(c.passed)] for c in checks], "failed_calls": failed_calls}


def diff_keys(a: dict, b: dict) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if json.dumps(a.get(k), sort_keys=True, default=str) != json.dumps(b.get(k), sort_keys=True, default=str):
            out.append(k)
    return out


def passing_attempts(task_id: str, cap: int) -> list[tuple[str, dict, int]]:
    rows = []
    for rj in glob.glob(f"{ROOT}/runs/*/*/{task_id}/result.json"):
        try:
            rec = json.load(open(rj))
        except Exception:  # noqa: BLE001
            continue
        if not rec.get("passed"):
            continue
        tj = Path(rj).parent / "trajectory.json"
        if not tj.is_file():
            continue
        tr = json.loads(tj.read_text())
        rows.append((rec.get("model", "?"), tr, int(tr.get("total_steps") or 999)))
    rows.sort(key=lambda r: (r[2], r[0]))
    return rows if cap <= 0 else rows[:cap]


def static_scan(spec: TaskSpec) -> dict:
    snap = ROOT / "environments" / spec.environment.name / "snapshots" / spec.environment.snapshot
    files = [p for p in sorted(snap.rglob("*")) if p.is_file() and p.suffix in {".py", ".sh", ".json", ".md"}]
    hidden = ROOT / "evalkit/envs/hidden_tests" / f"{spec.environment.snapshot}.py"
    if hidden.is_file():
        files.append(hidden)
    found = {}
    for p in files:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for name, pat in HAZARDS.items():
            for m in pat.finditer(txt):
                seeded = name == "unseeded-random" and "random.Random(" in txt
                if seeded:
                    continue
                found.setdefault(name, []).append(f"{p.name}:{txt[:m.start()].count(chr(10)) + 1}")
    missing = []
    src = snap / "source.json"
    if src.is_file():
        try:
            target = ROOT / json.loads(src.read_text())["path"]
            if not target.is_file():
                missing.append(str(target))
        except Exception:  # noqa: BLE001
            missing.append("source.json unreadable")
    return {"hazards": {k: v[:3] for k, v in found.items()}, "missing_assets": missing}


def budget_headroom(spec: TaskSpec, attempts: list) -> dict:
    used = {"steps": [], "tokens": [], "elapsed_s": []}
    for _, tr, _ in [(a[0], a[1], 0) for a in attempts]:
        used["steps"].append(tr.get("total_steps") or 0)
        used["tokens"].append(tr.get("total_tokens") or 0)
        used["elapsed_s"].append(tr.get("elapsed_s") or 0)
    if not used["steps"]:
        return {}
    return {"max_steps_used": max(used["steps"]), "max_tokens_used": max(used["tokens"]),
            "max_elapsed_used": round(max(used["elapsed_s"]), 1),
            "budget": [spec.budget.max_steps, spec.budget.max_tokens, spec.budget.timeout_s]}


def audit_one(spec_id: str, spec: TaskSpec, path: str, cap: int) -> dict:
    row = {"task": spec_id, "file": path, "env": f"{spec.environment.name}/{spec.environment.snapshot}",
           "difficulty": spec.difficulty, "fingerprint": task_fingerprint(spec, ROOT)[:12],
           "rubric": bool(spec.rubric), "verdict": None, "evidence": None, "note": "",
           "timing_asserts": timing_dependent_asserts(spec)}
    static = static_scan(spec)
    row["hazards"] = static["hazards"]
    row["missing_assets"] = static["missing_assets"]

    def stability(a_state, b_state, a_ok, b_ok):
        row["repeatable"] = (a_ok == b_ok)
        row["state_digests"] = [state_digest(a_state), state_digest(b_state)]
        row["state_diff_keys"] = diff_keys(a_state, b_state) if a_state != b_state else []
        if not row["repeatable"]:
            return "UNSTABLE"
        return "UNSTABLE_STATE" if row["state_diff_keys"] else "SOLVABLE+STABLE"

    # 先算好预算余量：参考产物通过时会提前 return，不能因此丢掉这一列
    attempts = passing_attempts(spec_id, 0)
    row["budget"] = budget_headroom(spec, attempts)

    ref = None
    if reference_path(spec_id).is_file():
        try:
            calls = json.loads(reference_path(spec_id).read_text())["calls"]
            ok_a, state_a, detail = replay_calls(spec, calls)
            ok_b, state_b, _ = replay_calls(spec, calls)
            ref = (ok_a, ok_b, state_a, state_b, detail)
        except Exception as ex:  # noqa: BLE001
            row["reference_error"] = f"{type(ex).__name__}: {str(ex)[:140]}"
    else:
        row["reference"] = "missing"

    if ref:
        ok_a, ok_b, state_a, state_b, detail = ref
        # 参考解里出错的调用不自动判死——有些题的正确解就要先复现一次失败
        # （script-dev-008 要用原命令复现 cwd bug）。但必须显式列出来让人判：
        # 否则"参数名写错被静默吞掉、只靠终态碰巧对上"也算证明了可解。
        row["reference_failed_calls"] = detail.get("failed_calls") or []
        hidden = (state_a or {}).get("hidden_tests") or {}
        row["evidence"] = {"kind": "reference", "steps": len(detail["checks"]),
                           "hidden": f"{hidden.get('passed')}/{hidden.get('total')}" if hidden.get("applicable") else "-"}
        if ok_a:
            row["verdict"] = stability(state_a, state_b, ok_a, ok_b)
            return row
        row["reference_failing_checks"] = [c for c in detail["checks"] if not c[1]][:6]
        row["note"] = "参考产物在当前闸门下不过（题面变了或参考写坏了）"

    attempts = attempts if cap <= 0 else attempts[:cap]
    if not attempts:
        row["verdict"] = row["verdict"] or "NO_HISTORY"
        row["note"] = row["note"] or "无历史通过记录（多半是新题）、且无 docs/hardening_evidence/references/ 参考产物"
        return row

    for model, tr, _ in attempts:
        try:
            checks, state = replay(spec, tr)
        except Exception as ex:  # noqa: BLE001
            row["note"] = f"replay error: {type(ex).__name__}: {str(ex)[:90]}"
            continue
        ok = all(c.passed for c in checks) if checks else False
        if not ok:
            continue
        try:
            checks2, state2 = replay(spec, tr)
        except Exception as ex:  # noqa: BLE001
            row["verdict"] = "ERROR"
            row["note"] += f" | 二次回放异常 {type(ex).__name__}"
            return row
        ok2 = all(c.passed for c in checks2) if checks2 else False
        hidden = (state or {}).get("hidden_tests") or {}
        row["evidence"] = {"kind": "model", "model": model, "steps": tr.get("total_steps"),
                           "hidden": f"{hidden.get('passed')}/{hidden.get('total')}" if hidden.get("applicable") else "-"}
        row["verdict"] = stability(state, state2, ok, ok2)
        row["budget"] = budget_headroom(spec, attempts)
        return row

    row["verdict"] = row["verdict"] or "UNSOLVED-BY-HISTORY"
    row["note"] = row["note"] or (f"{len(attempts)} 条历史通过轨迹在当前闸门下全部失败——"
                                  "要么闸门加过头，要么样本已被新规则作废；缺参考产物所以无法判可解")
    row["budget"] = budget_headroom(spec, attempts)
    return row


def timing_sanitizer_selftest() -> list[str]:
    """回归护栏：计时/装饰变化不得改变终态。

    真实教训：第一版把计时尾正则锚在行尾，而 code_repo 跑 pytest 不带 -q，
    最后一行是 "==== 9 passed in 0.02s ===="，锚 $ 的正则从未命中，
    同一份解在 0.01s/0.02s 之间产出不同终态却没人发现。
    """
    from evalkit.envs.code_repo import CodeRepoEnv as C
    pairs = [
        ("=== 9 passed in 0.02s ======", "=== 9 passed in 0.01s ======"),
        ("= 1 failed, 8 passed in 0.15s =", "= 1 failed, 8 passed in 0.09s ="),
        ("= 2 errors in 0.30s =", "= 2 errors in 0.31s ="),
        ("12 passed in 1.23s", "12 passed in 60.00s"),
    ]
    bad = []
    for a, b in pairs:
        if C._parse_pytest(a) != C._parse_pytest(b):
            bad.append(f"{a!r} vs {b!r} -> 终态不同")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", help="逗号分隔的题 id 子集")
    ap.add_argument("--cap", type=int, default=0, help="每题最多回放几条历史通过轨迹")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json")
    a = ap.parse_args()

    specs = load_specs()
    sanitizer_bad = timing_sanitizer_selftest()
    if sanitizer_bad:
        print("!! 计时归一化回归（终态会随耗时漂移，先修 _parse_pytest 再谈别的）:")
        for s in sanitizer_bad:
            print("   ", s)
    else:
        print("计时归一化自检通过（4 组耗时/装饰变化均不改变终态）")
    if a.tasks:
        want = set(a.tasks.split(","))
        specs = {k: v for k, v in specs.items() if k in want}
    rows = []
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futs = {pool.submit(audit_one, k, s, p, a.cap): k for k, (s, p) in specs.items()}
        for fut in cf.as_completed(futs):
            k = futs[fut]
            try:
                r = fut.result()
            except Exception as ex:  # noqa: BLE001
                r = {"task": k, "verdict": "ERROR", "note": f"{type(ex).__name__}: {str(ex)[:120]}",
                     "rubric": False, "hazards": {}, "missing_assets": []}
            rows.append(r)
            print(f"  {r['verdict']:18s} {r['task']}" + (f"  <- {r['note'][:70]}" if r.get("note") else ""), flush=True)
    rows.sort(key=lambda r: r["task"])

    order = {"UNSTABLE": 0, "UNSTABLE_STATE": 1, "ERROR": 2, "UNSOLVED-BY-HISTORY": 3,
             "NO_HISTORY": 4, "SOLVABLE+STABLE": 5}
    print("\n=== 汇总 ===")
    for v in sorted(set(r["verdict"] for r in rows), key=lambda x: order.get(x, 9)):
        print(f"  {v:20s} {sum(1 for r in rows if r['verdict'] == v)}")
    print(f"  带 rubric(受 LLM 裁判影响) {sum(1 for r in rows if r['rubric'])} / {len(rows)}")
    for label, key in (("断言读到计时字段", "timing_asserts"), ("参考产物报错", "reference_error"),
                       ("参考产物不过", "reference_failing_checks"), ("资产缺失", "missing_assets")):
        hit = [r for r in rows if r.get(key)]
        if hit:
            print(f"  {label}: {len(hit)} 题")
            for r in hit[:8]:
                print(f"    {r['task']}: {str(r[key])[:150]}")
    hz = [r for r in rows if r.get("hazards")]
    print(f"  静态隐患题数 {len(hz)}")
    for r in hz:
        print(f"    {r['task']}: {r['hazards']}")
    for v in ("UNSOLVED-BY-HISTORY", "NO_HISTORY"):
        names = [r["task"] for r in rows if r["verdict"] == v]
        if names:
            print(f"  {v} ({len(names)}): {', '.join(names)}")
    rfc = [r for r in rows if r.get("reference_failed_calls")]
    if rfc:
        print(f"  参考解内含出错调用（须人工确认是「先复现失败」还是参数写错）: {len(rfc)} 题")
        for r in rfc:
            print(f"    {r['task']}: {r['reference_failed_calls']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"明细 -> {a.json}")
    bad = [r for r in rows if r["verdict"] not in ("SOLVABLE+STABLE",)]
    print(f"\n不成立 {len(bad)} / {len(rows)}" + ("" if not sanitizer_bad else "  (+ 计时归一化回归)"))
    return 1 if (bad or sanitizer_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
