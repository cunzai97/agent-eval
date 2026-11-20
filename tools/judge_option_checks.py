#!/usr/bin/env python3
"""裁判可选化（默认档 mimo-flash）的回归检查：不发车、不烧 token。

覆盖三层，缺一层都会漏 bug：

1. **档案解析**（evalkit.judge）：新旧两种 models.yaml 形状、三态选择口径
   （开+默认 / 开+点名 / 关）、未知档名报错时要把可用档案列出来。
2. **服务端闸门**（web_server.RunManager）：面板能拿到尺子清单与默认档；密钥没就绪的
   档位必须在发车前被拦住；续跑/重判优先沿用 manifest 里那把原尺。
3. **CLI 行为**：`--judge` 省略档名＝默认档、`--no-judge`＝关掉、乱写档名退码 2。
   只跑 `rejudge --dry-run`（它不写盘、不调裁判端点）。

前端只做静态断言：面板发的是档案名字符串（不是布尔勾选框），命令预览带上真实选择。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from evalkit.judge import (JUDGE_DEFAULT, judge_identity, judge_profiles,  # noqa: E402
                           parse_judge_choice, resolve_judge_profile)

FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"  <- {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def models_yaml() -> dict:
    return yaml.safe_load((ROOT / "models.yaml").read_text(encoding="utf-8"))


# ---------- 1. 档案解析 ----------
data = models_yaml()
profiles, default = judge_profiles(data)
check("models.yaml 的 judge 段是 profiles+default 形状", bool(profiles) and default is not None,
      f"profiles={sorted(profiles)} default={default}")
check("默认档是 mimo-flash（mimo-v2.6-flash）",
      default == "mimo-flash" and profiles.get("mimo-flash", {}).get("model") == "mimo-v2.6-flash",
      f"default={default} model={profiles.get('mimo-flash', {}).get('model')}")
check("保留 luna-6 作为可选尺（与历史行同尺那把）", "luna-6" in profiles)
check("裁判档案不把密钥值外发成明文",
      all(str(cfg.get("api_key", "")).startswith("${") for cfg in profiles.values()),
      str({k: v.get("api_key") for k, v in profiles.items()}))
check("档案名注入进 identity",
      judge_identity(resolve_judge_profile(data)) == "mimo-flash:mimo-v2.6-flash",
      judge_identity(resolve_judge_profile(data)))

legacy = {"judge": {"name": "old-judge", "base_url": "http://x/v1", "model": "m", "api_key": "k"}}
lp, ldef = judge_profiles(legacy)
check("旧单档案形状仍能被解析（向后兼容）",
      set(lp) == {"old-judge"} and ldef == "old-judge", f"{sorted(lp)} {ldef}")

table = [
    (True, JUDGE_DEFAULT, "老客户端 true = 开、用默认档"),
    ("default", JUDGE_DEFAULT, "'default' = 跟随 judge.default"),
    ("", None, "空串 = 不启用（沉默不烧 token）"),
    ("off", None, "'off' = 不启用"),
    (False, None, "false = 不启用"),
    (None, None, "不发字段 = 不启用"),
    ("mimo-flash", "mimo-flash", "点名档案 = 用它"),
]
for value, expected, label in table:
    check(f"parse_judge_choice({value!r}) → {expected!r}｜{label}",
          parse_judge_choice(value) == expected, repr(parse_judge_choice(value)))

try:
    resolve_judge_profile(data, "no-such-ruler")
    check("未知裁判档案被拒并列可用清单", False, "没抛 ValueError")
except ValueError as exc:
    check("未知裁判档案被拒并列可用清单",
          "可用" in str(exc) and "mimo-flash" in str(exc), str(exc))

# ---------- 2. 服务端闸门 ----------
import web_server  # noqa: E402

manager = web_server.RunManager()
view = manager.config()
check("/api/config 暴露 judges + judgeDefault，且不再发旧的 judgeAvailable 布尔",
      isinstance(view.get("judges"), list) and view.get("judgeDefault") == "mimo-flash"
      and "judgeAvailable" not in view, json.dumps(view.get("judges"), ensure_ascii=False)[:200])
rows = {row["name"]: row for row in view["judges"]}
check("每档都带密钥就绪状态（面板要能标出缺哪把）",
      all({"keyReady", "keySource", "isDefault"} <= set(row) for row in view["judges"]),
      str(sorted(rows)))
ready_names = [row["name"] for row in view["judges"] if row["keyReady"]]
check("至少一把尺此刻可用（.env/环境里有密钥）", bool(ready_names), "全部缺密钥")

if ready_names:
    name = ready_names[0]
    resolved = manager.resolve_judge(name)
    check(f"resolve_judge('{name}') 返回该档案", resolved is not None and resolved["name"] == name,
          str(resolved))
    follow = manager.resolve_judge(True)   # 看板发车：不带 judge 字段等价于 True
    check("看板发车（judge 字段缺省=True）用默认档且密钥就绪",
          follow is not None and follow["name"] in ready_names, str(follow))
check("resolve_judge('off') 与 false 都是关掉裁判",
      manager.resolve_judge("off") is None and manager.resolve_judge(False) is None)
try:
    manager.resolve_judge("no-such-ruler")
    check("面板点名不存在的尺被拦住（ValueError→400）", False, "没抛异常")
except ValueError as exc:
    check("面板点名不存在的尺被拦住（ValueError→400）", "可用" in str(exc), str(exc))
for candidate in ("luna-6", "mimo-flash"):
    try:
        manager.resolve_judge(candidate)
        ready_flag = True
    except ValueError as exc:
        ready_flag = "缺密钥" in str(exc)
    check(f"{candidate} 要么可用、要么被明确报成缺密钥", ready_flag, "")

def try_run_judge(manifest: dict, *, enabled: bool) -> tuple[dict | None, str]:
    """跑一把 run_judge，把"缺密钥"这类拦下也当成结果返（检查脚本不该因此抛异常）。"""
    try:
        return manager.run_judge(manifest, enabled=enabled)
    except ValueError as exc:
        return {"name": f"<拦截: {exc}>"}, str(exc)


manifest_luna = {"judge": True, "judge_profile": "luna-6:gpt-6-luna"}
profile, note = try_run_judge(manifest_luna, enabled=True)
luna_ready = any(row["name"] == "luna-6" and row["keyReady"] for row in view["judges"])
check("续跑/重判优先沿用 manifest 里那把原尺",
      (profile or {}).get("name") == "luna-6" and not note if luna_ready
      else "缺密钥" in note,
      f"{profile} {note}")
profile, note = try_run_judge({"judge": True, "judge_profile": "ghost:v9"}, enabled=True)
check("原尺从表里消失时退到默认档并带回一句警告",
      profile is not None and profile.get("name") == default and bool(note), f"{profile} {note}")
profile, _ = try_run_judge({"judge": False}, enabled=False)
check("这一轮本来没开裁判 → 续跑也不开", profile is None, str(profile))

# ---------- 3. CLI 行为（只跑 rejudge --dry-run：不写盘、不调端点）----------
run_dir = ROOT / "runs" / "deepseek-v4-flash-0921"


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args],
                          capture_output=True, text=True, timeout=180, cwd=str(ROOT))


if run_dir.is_dir():
    base = ["rejudge", "--out", str(run_dir), "--dry-run"]
    out_default = cli(*base)
    check("CLI 不带 --judge 时用默认档当尺",
          out_default.returncode == 0 and "judge=mimo-flash:mimo-v2.6-flash" in out_default.stdout,
          out_default.stdout.strip()[:200] + out_default.stderr.strip()[:200])
    out_named = cli(*base, "--judge", "luna-6")
    check("CLI --judge luna-6 换到那把老尺",
          out_named.returncode == 0 and "judge=luna-6:gpt-6-luna" in out_named.stdout,
          out_named.stdout.strip()[:200])
    out_bad = cli(*base, "--judge", "no-such-ruler")
    check("CLI 写错档案名：退码 2 且列出可用清单",
          out_bad.returncode == 2 and "可用" in out_bad.stderr, out_bad.stderr.strip()[:200])

# ---------- 4. 前端静态断言 ----------
html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
console = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
check("面板裁判控件是下拉框（含「不启用」选项位）",
      'select id="runJudge"' in html and 'id="judgeHint"' in html)
check("面板发的是档案名字符串而不是勾选框布尔",
      re.search(r"function judgeSelection\(\)", app) is not None
      and "judge: judgeSelection()" in app and "#runJudge').checked" not in app)
check("命令预览把裁判选择显示出来（--judge NAME / --no-judge）",
      '--no-judge' in app and "--judge ${judgeChoice}" in app)
check("控制台徽标显示用了哪把尺", "state.judgeProfile" in console)

if FAILS:
    print(f"\n失败 {len(FAILS)} 项：{' | '.join(FAILS)}")
else:
    print("\n全部通过")
sys.exit(1 if FAILS else 0)
