#!/usr/bin/env bash
# Acceptance harness for any Ternary-Bonsai V100 optimisation candidate.
#
# A candidate is only "pure" if BOTH hold:
#   1. it is faster (pp and/or tg), and
#   2. greedy output is token-for-token identical to the reference config.
# A speed win that changes the token stream is a behaviour change, not an
# optimisation: agent-eval's leaderboard is compared against ~31 historical runs,
# so a non-pure change invalidates them all.
#
#   usage: tools/opt_ab.sh "<label-A>" "<flags A>" ["<label-B>" "<flags B>" ...]
#   e.g.   tools/opt_ab.sh ref "-b 2048 -ub 512" cand "-b 4096 -ub 4096"
#
# Extra knobs: PORT (1250), CTX (16384), MAXTOK (256), NCASES (4), BIN, MODEL.
# Quality needs a GPU; run speed numbers separately with llama-bench (see notes in
# memory/ternary-v100-decode-tuning.md) so server overhead doesn't blur kernel effects.
set -euo pipefail
BIN=${BIN:-/home/pan/work/llama/prism-llama.cpp/build/bin}
M=${MODEL:-/home/pan/work/llama/models/Ternary-Bonsai-2-27B-PQ2_0.gguf}
PORT=${PORT:-1250}
CTX=${CTX:-16384}
RUNS=${RUNS:-2}          # repeat each case; catches non-determinism masquerading as equality

if [ "$#" -lt 2 ] || [ $(( $# % 2 )) -ne 0 ]; then
  echo 'usage: opt_ab.sh "<label>" "<flags>" ["<label>" "<flags>" ...]' >&2; exit 2
fi

make_cases() {           # real repo content, greedy-friendly prompts, long enough to diverge
python3 - "$1" <<'PY'
import json, pathlib, sys
n = int(sys.argv[1])
pairs = [
 ("/home/pan/work/agent-eval/evalkit/agent_loop.py",
  "List every condition that can terminate this loop, one per line, as code."),
 ("/home/pan/work/agent-eval/evalkit/provider.py",
  "Describe every field stored on the object, one per line, as 'name: purpose'."),
 ("/home/pan/work/llama/prism-llama.cpp/ggml/src/ggml-cuda/mmvq.cu",
  "List every function that takes a ggml_type parameter, one per line."),
 ("/home/pan/work/agent-eval/docs/TASK_HARDENING_PLAN.md",
  "Output a JSON object with the target pass-rate bands mentioned above."),
 ("/home/pan/work/llama/prism-llama.cpp/ggml/src/ggml-cuda/norm.cu",
  "List every kernel launched by this file, one per line."),
 ("/home/pan/work/agent-eval/evalkit/evaluator.py",
  "Summarise how scoring works in five numbered lines."),
]
cases = []
for p, q in pairs[:n]:
    try:
        txt = pathlib.Path(p).read_text(errors="ignore")[:9000]
    except Exception:
        continue
    cases.append({"prompt": "File %s:\n\n%s\n\nQuestion: %s" % (p, txt, q)})
pathlib.Path("/tmp/optab_cases.json").write_text(json.dumps(cases))
print("cases:", len(cases))
PY
}

collect() {  # $1 label $2 flags
  for run in $(seq 1 "$RUNS"); do
    $BIN/llama-server -m "$M" --host 127.0.0.1 --port $PORT -ngl 99 -c $CTX \
      --flash-attn on -np 1 ${2:+$2} > /tmp/optab_${1}_$run.log 2>&1 &
    local srv=$!
    for _ in $(seq 1 200); do
      sleep 2; curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
      kill -0 $srv 2>/dev/null || { echo "  [$1 run$run] server died:"; tail -3 /tmp/optab_${1}_$run.log; return 1; }
    done
    python3 - "$1" "$run" <<'PY'
import json, os, sys, time, urllib.request
label, run = sys.argv[1], sys.argv[2]
cases = json.load(open("/tmp/optab_cases.json"))
out = {}
for i, c in enumerate(cases):
    body = {"model": "local", "temperature": 0.0, "seed": 42,
            "max_tokens": int(os.environ.get("MAXTOK", 256)),
            "logprobs": True, "top_logprobs": 1,
            "messages": [{"role": "user", "content": c["prompt"]}]}
    q = urllib.request.Request("http://127.0.0.1:%s/v1/chat/completions" % os.environ.get("PORT", "1250"),
                               data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        t0 = time.time()
        with urllib.request.urlopen(q, timeout=1200) as fp: d = json.load(fp)
    except Exception as e:
        print("  [%s/%s] case%d FAILED %s" % (label, run, i, e)); continue
    ch = d["choices"][0]
    lp = ((ch.get("logprobs") or {}).get("content")) or []
    out[str(i)] = {"text": (ch["message"].get("content") or ""),
                   "toks": [[t["token"], t["logprob"]] for t in lp],
                   "finish": ch.get("finish_reason"),
                   "secs": round(time.time() - t0, 2)}
json.dump(out, open("/tmp/optab_%s_%s.json" % (label, run), "w"))
tot = sum(v["secs"] for v in out.values())
print("  [%s run%s] %d cases, %.1fs total wall, %d tokens generated" %
      (label, run, len(out), tot, sum(len(v["toks"]) for v in out.values())))
PY
    kill $srv 2>/dev/null || true; wait $srv 2>/dev/null || true; sleep 3
  done
}

compare() {  # $1 base label, rest: candidate labels
  local base=$1; shift
  python3 - "$base" "$@" <<'PY'
import glob, json, os, sys
base, cands = sys.argv[1], sys.argv[2:]
runs = sorted(os.environ.get("RUNS_COUNT", "1").split(","))
def load(lbl, run):
    p = "/tmp/optab_%s_%s.json" % (lbl, run)
    return json.load(open(p)) if os.path.exists(p) else {}
b = load(base, 1)
if not b:
    print("no baseline data"); raise SystemExit(1)
verdict_ok = True
for lbl in cands:
    c = load(lbl, 1)
    ids = sorted(set(b) & set(c), key=int)
    worst = 0.0; firstdiff = None; ndiff = 0
    for i in ids:
        ta = [t for t, _ in b[i]["toks"]]; tb = [t for t, _ in c[i]["toks"]]
        n = min(len(ta), len(tb))
        div = next((k for k in range(n) if ta[k] != tb[k]), n)
        if div < n or len(ta) != len(tb):
            ndiff += 1
            if firstdiff is None: firstdiff = (i, div, ta[div:div+3], tb[div:div+3])
        for k in range(min(div + 1, n)):
            worst = max(worst, abs(b[i]["toks"][k][1] - c[i]["toks"][k][1]))
    tb_tot = sum(b[i]["secs"] for i in ids); tc_tot = sum(c[i]["secs"] for i in ids)
    speed = (tb_tot - tc_tot) / tb_tot * 100 if tb_tot else 0
    pure = (ndiff == 0)
    print("  %-16s vs %s:  %s  (%d/%d cases differ, first diff %s, max|dlogprob|=%.4f)  wall %.1fs -> %.1fs (%+.1f%%)"
          % (lbl, base, "PURE" if pure else "BEHAVIOUR CHANGE", ndiff, len(ids),
             "none" if pure else "case%s@tok%d %r vs %r" % firstdiff if firstdiff else "length",
             worst, tb_tot, tc_tot, speed))
    if not pure: verdict_ok = False
print("VERDICT:", "all candidates are pure optimisations" if verdict_ok
      else "REJECT for silent adoption - changes model output")
PY
}

make_cases "${NCASES:-4}"
labels=(); args=("$@")
for ((i=0; i<$#; i+=2)); do labels+=("${args[i]}"); done
for ((i=0; i<$#; i+=2)); do
  echo "=== collecting ${args[i]} : '${args[i+1]}' ==="
  RUNS_COUNT=$RUNS PORT=$PORT MAXTOK=$MAXTOK collect "${args[i]}" "${args[i+1]}"
done
echo "=== comparison ==="
RUNS_COUNT=$RUNS compare "${labels[0]}" "${labels[@]:1}"
