#!/usr/bin/env python3
"""Build the static dashboard payload from task YAML and a markdown run report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evalkit.schema import TaskSpec  # noqa: E402
from evalkit.fingerprint import task_fingerprint  # noqa: E402


def report_rows(path: Path) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    in_details = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line == "## 任务明细":
            in_details = True
            continue
        if not in_details or not raw_line.startswith("|"):
            continue
        cells = [cell.strip() for cell in raw_line.strip("|").split("|")]
        if len(cells) != 9 or cells[0] in {"model", "---"} or set(cells[0]) == {"-"}:
            continue
        model, task_id, passed, steps, tokens, elapsed, speed, judge, failure = cells
        if not steps.isdigit():
            continue
        rows[task_id] = {
            "model": model,
            # Y=通过 N=失败 U=未评（见 cli.py cmd_report）：UNGRADED 在前端与
            # STALE 一样被排除出通过率分母，而不是画成一次能力失败。
            "status": {"Y": "PASS", "U": "UNGRADED"}.get(passed, "FAIL"),
            "steps": int(steps),
            "tokens": int(tokens),
            "elapsed": float(elapsed),
            "speed": float(speed),
            "judge": None if judge == "-" else judge,
            "failure": failure,
        }
    return rows


def task_payload(
    tasks_dir: Path,
    results: dict[str, dict[str, object]],
    run_fingerprints: dict[str, str],
) -> list[dict[str, object]]:
    payload = []
    for task_path in sorted(tasks_dir.rglob("*.yaml")):
        task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        agent = task.get("agent") or {}
        fingerprint = task_fingerprint(TaskSpec(**task), ROOT)
        result = results.get(task["id"])
        stale = bool(result and run_fingerprints.get(task["id"]) != fingerprint)
        if result:
            result = {**result, "stale": stale}
        payload.append(
            {
                "id": task["id"],
                "title": task["title"],
                "category": task["category"],
                "tags": task.get("tags", []),
                "difficulty": task.get("difficulty", "unknown"),
                "environment": task.get("environment", {}),
                "systemPrompt": agent.get("system_prompt", "").strip(),
                "tools": agent.get("tools", []),
                "prompt": task.get("task_prompt", "").strip(),
                "budget": task.get("budget", {}),
                "success": task.get("success", []),
                "rubric": task.get("rubric"),
                "source": str(task_path.relative_to(ROOT)),
                "fingerprint": fingerprint,
                "result": result,
            }
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="runs/full-38-8080/report.md")
    parser.add_argument("--output", default="web/data.js")
    args = parser.parse_args()

    report = ROOT / args.report
    output = ROOT / args.output
    results = report_rows(report)
    manifest_path = report.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    tasks = task_payload(ROOT / "tasks", results, manifest.get("task_fingerprints", {}))
    report_text = report.read_text(encoding="utf-8")
    # 新表头（2026-09-21 起）：model | passed | graded | ungraded | rate | steps | tokens | elapsed | speed
    summary_match = re.search(
        r"\| [^|]+ \| (\d+) \| (\d+) \| (\d+) \| (\d+)% \| ([\d.]+) \| (\d+) \| ([\d.]+) \| ([\d.]+) \|",
        report_text,
    )
    summary_mode = "graded"
    if not summary_match:
        # 旧表头：model | passed | total | rate | steps | tokens | elapsed | speed
        summary_match = re.search(
            r"\| [^|]+ \| (\d+) \| (\d+) \| (\d+)% \| ([\d.]+) \| (\d+) \| ([\d.]+) \| ([\d.]+) \|",
            report_text,
        )
        summary_mode = "legacy"
    summary = {}
    if summary_match:
        if summary_mode == "graded":
            passed, graded, ungraded, rate, avg_steps, avg_tokens, avg_elapsed, speed = summary_match.groups()
            total, summary["ungraded"] = int(graded), int(ungraded)
        else:
            passed, total, rate, avg_steps, avg_tokens, avg_elapsed, speed = summary_match.groups()
            summary["ungraded"] = 0
        summary = {
            "passed": int(passed),
            "total": int(total),
            "rate": int(rate),
            "avgSteps": float(avg_steps),
            "avgTokens": int(avg_tokens),
            "avgElapsed": float(avg_elapsed),
            "speed": float(speed),
            **summary,
        }
    data = {
        "run": report.parent.name,
        "generatedFrom": str(report.relative_to(ROOT)),
        "startedAt": manifest.get("started_at"),
        "jobs": manifest.get("jobs"),
        "summary": summary,
        "tasks": tasks,
    }
    output.write_text(
        "// Generated by tools/build_web_data.py; do not edit by hand.\n"
        + "window.BENCH_DATA = "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote {output.relative_to(ROOT)} with {len(tasks)} tasks and {len(results)} results")


if __name__ == "__main__":
    main()
