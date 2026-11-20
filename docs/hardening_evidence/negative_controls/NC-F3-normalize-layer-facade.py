"""Negative control for fix-failing-test-003 (snapshot fix-003): the facade repair.

Every visible behaviour is delivered, and delivered *correctly* -- through the
public entry point. The trick is where the work happens: `text/normalize.py` keeps
its defect (`return value.lower()`, which cannot fold "ß", "ﬁ", "Ⅻ" or a combining
mark and raises AttributeError on a non-string) and gains only a comment saying the
folding moved, while `text/slug.py` re-does the normalization itself and still calls
`normalize_text` first, so the call graph looks like a delegation.

That shape is exactly what this task's own checks cannot see:

* `state.test_report` -- the visible suite only ever calls `slugify`, so it is green;
* `files_changed` -- a one-line comment inside `normalize_text` counts as having
  repaired that file, which is what the `'text/normalize.py' in state.files_changed`
  assertion demands;
* the prompt warns that "规范化和 slug 层均可能有问题", and the task's whole point is
  that the normalization layer is a real API with its own contract. Left as a stub,
  every *other* caller of `normalize_text` still gets broken text.

Only the hidden layer probes catch it: `test_the_normalize_layer_does_its_own_normalization`
plus its six non-string cases.

    python3 docs/hardening_evidence/negative_controls/NC-F3-normalize-layer-facade.py

Expected: visible 8/8 green, the other four checks PASS, hidden 310/317 -> FAIL.
Measured 2026-09-21: `7 failed, 310 passed in 0.09s`, i.e. the whole behaviour
sweep (separators, apostrophes, cut lengths, rejected types, folding shapes) stays
green through the facade -- only the normalization layer's own duties go red.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml                                               # noqa: E402
from evalkit.envs import get_env                          # noqa: E402
from evalkit.schema import TaskSpec, Trajectory           # noqa: E402
from evalkit.verifier import run_checks                   # noqa: E402

NORMALIZE = '''import unicodedata


def normalize_text(value):
    # NOTE: the case/accent folding now happens inside slug.slugify, which is the
    # only caller that needs it. Kept as plain lowercasing on purpose.
    return value.lower()
'''

SLUG = '''import re
import unicodedata

from .normalize import normalize_text

_APOSTROPHES = "\\u0027\\u2018\\u2019\\u0060\\u02bc"
_SEPARATOR = re.compile(r"[^0-9a-z]+")


def _fold(text):
    """NFKD + casefold + drop combining marks (done here, not in normalize.py)."""
    text = unicodedata.normalize("NFKD", text).casefold()
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def slugify(value, max_length=80):
    if not isinstance(value, str):
        raise TypeError(f"expected str, got {type(value).__name__}")
    text = _fold(normalize_text(value))
    for apostrophe in _APOSTROPHES:
        text = text.replace(apostrophe, "")
    words = [word for word in _SEPARATOR.split(text) if word]
    if not words:
        raise ValueError("value has no letters or digits left to slugify")
    kept = []
    length = 0
    for word in words:
        added = len(word) + (1 if kept else 0)
        if length + added > max_length:
            break
        kept.append(word)
        length += added
    if not kept:
        raise ValueError(f"max_length={max_length} cannot hold a whole word")
    return "-".join(kept)
'''

SNAP_FILES = ("text/normalize.py", "text/slug.py")
EDITS = [
    ("text/normalize.py", "def normalize_text(value):\n    return value.lower()",
     NORMALIZE[len("import unicodedata\n\n\n"):] if False else NORMALIZE.split("\n\n\n", 1)[1]),
    ("text/slug.py", "import re\n\nfrom .normalize import normalize_text",
     SLUG.split("\n\n\ndef _fold", 1)[0] + "\n\n\ndef _fold", ),
]


def _replace_whole_file(env, path: str, content: str) -> bool:
    """Rewrite a file through the env's own edit_file tool (no direct fs writes)."""
    current = env.call_tool("read_file", {"path": path}).result["content"]
    head = current.rstrip("\n")
    if not env.call_tool("edit_file", {"path": path, "old_text": head,
                                       "new_text": content.rstrip("\n")}).ok:
        return False
    return True


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/fix-and-test/fix-failing-test-003.yaml", encoding="utf-8")))
    steps = []
    with tempfile.TemporaryDirectory(prefix="ncf3-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        # 复现基线：先跑一次测试，与任何真实解的顺序一致
        env.call_tool("run_tests", {})
        for index, path in enumerate(SNAP_FILES, 1):
            content = NORMALIZE if path.endswith("normalize.py") else SLUG
            if not _replace_whole_file(env, path, content):
                print(f"!! shortcut edit #{index} on {path} refused")
                return 1
            args = {"path": path, "old_text": "<previous file body>", "new_text": "<shortcut body>"}
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": f"e{index}", "name": "edit_file", "args": args}],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    for idx, name in enumerate(("run_tests", "done"), len(steps) + 1):
        steps.append({"idx": idx, "thought": "", "tokens": {}, "latency_ms": 0,
                      "tool_calls": [{"id": f"c{idx}", "name": name, "args": {}}],
                      "observations": []})
    traj = {"task_id": spec.id, "model": "negative-control", "seed": 0,
            "started_at": "1970-01-01T00:00:00Z", "steps": steps, "termination": "done",
            "total_steps": len(steps) + 2, "total_tokens": 0, "elapsed_s": 0.0, "error": None}
    checks = run_checks(spec, Trajectory(**traj), state)
    report = state.get("test_report") or {}
    hidden = state.get("hidden_tests") or {}
    print("visible pytest:", {k: report.get(k) for k in ("passed", "failed", "errors")})
    print("files_changed :", state.get("files_changed"))
    print("hidden result : %s/%s applicable=%s" % (hidden.get("passed"), hidden.get("total"),
                                                   hidden.get("applicable")))
    for c in checks:
        print("  [%s] %s" % ("PASS" if c.passed else "FAIL", c.detail[:115]))
    caught = (hidden.get("applicable") and report.get("failed") == 0
              and hidden.get("passed", 0) < hidden.get("total", 1))
    print("\nVERDICT:", "visible green + hidden red -> the gate covers the gap" if caught
          else "NOT caught -> the gate adds nothing")
    if caught:
        print((hidden.get("failures") or [""])[0][:2600])
    return 0 if caught else 2


if __name__ == "__main__":
    raise SystemExit(main())
