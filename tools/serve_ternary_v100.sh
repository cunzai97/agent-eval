#!/usr/bin/env bash
# Serve Ternary-Bonsai-2-27B (PQ2_0) on the V100 at port 1250 for agent-eval.
#
# Deliberately NOT 1235/1240: the 1235 python gateway binds its own upstream to
# 1240, and right now 1240 is serving qwen3.8-27b on the RX 7900 XTX. Pointing the
# `ternary-bonsai-27b` row of models.yaml at 1240 while that is up silently
# benchmarks the WRONG MODEL. This script refuses to start if 1250 is occupied and
# proves the served model id before exiting successfully.
#
# Verified 2026-09-20 on this box at -c 81920 -ub 4096: loads in 13.9 GiB of 16.4 GiB
# VRAM, pp 1064 tok/s, decode 53.3 tok/s (2k-6.7k ctx). The -ub 4096 figure is measured,
# the default below is deliberately the historical -ub 512 (see BATCH/UBATCH note).
#
# Tuning rationale (all measured, see memory/ternary-v100-decode-tuning.md):
#   BATCH/UBATCH    2048/512 on purpose. -ub 4096 is 13.5% faster end-to-end, but the
#                   tools/opt_ab.sh purity A/B showed greedy output DIVERGES in 2/5 cases
#                   ('%' vs 'percent' at token 237, max|dlogprob| 0.0307). That is a
#                   behaviour change, not an optimisation: runs would stop being
#                   comparable with the historical baselines. Only raise it if you intend
#                   to re-baseline every model. Override with UB=4096 ./this-script.
#   --flash-attn on fa=off loses 44% of decode at 32k ctx (30.6 vs 44.1 tok/s).
#   f16 KV cache    q8_0 KV is NOT faster here (44.05 -> 42.58 at 32k); use it only
#                   if you need more context than fits.
#   no --spec-type  ngram speculation measured SLOWER: 56.0 -> 49.7 tok/s (11-17% hit rate).
#   no draft-mtp    this GGUF carries no MTP tensors, so the head model has nothing to draft from.
#
# CONCURRENCY: run agent-eval with --jobs 1 against this server. provider.py re-sends the
# whole message history every step and llama.cpp's prefix reuse is per-slot; interleaving
# several tasks into one slot destroys the hit rate. Measured: a fully-repeated request
# prefills 4 tokens in 116ms when the slot is used by one task, while "same prefix + new
# tail" still pays 3.5s for 3.9k tokens. -np 1 below is deliberate, not a limit.
set -euo pipefail

BIN=${BIN:-/home/pan/work/llama/prism-llama.cpp/build/bin/llama-server}
MODEL=${MODEL:-/home/pan/work/llama/models/Ternary-Bonsai-2-27B-PQ2_0.gguf}
PORT=${PORT:-1250}
CTX=${CTX:-81920}
BATCH=${BATCH:-2048}
UBATCH=${UBATCH:-512}
LOG=${LOG:-/tmp/ternary_v100_${PORT}.log}

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PORT}\$"; then
  echo "port ${PORT} is already in use - refusing to start (never touch 1235/1240)" >&2
  exit 1
fi

echo "starting ${BIN##*/} on :${PORT} (log: ${LOG})"
"$BIN" -m "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  -ngl 99 -c "$CTX" -b "$BATCH" -ub "$UBATCH" \
  --flash-attn on -np 1 -t 8 \
  > "$LOG" 2>&1 &
SRV=$!

for _ in $(seq 1 300); do
  sleep 2
  curl -fs "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
  kill -0 $SRV 2>/dev/null || { echo "server died, last lines:" >&2; tail -20 "$LOG" >&2; exit 1; }
done

# Prove it is actually the ternary model and that it answers, before anyone trusts a run.
echo "pid=$SRV"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
curl -s "http://127.0.0.1:${PORT}/v1/models" | head -c 200; echo
# /props on this fork nests everything under default_generation_settings, and at default
# verbosity this build only logs n_ctx_slot / n_threads / kv_unified - it does NOT log
# n_gpu_layers or flash_attn (raise `-lv 3` / `-dgms` if you need that). So echo back
# exactly what it does report rather than guessing field names.
echo "--- applied config (from $LOG) ---"
grep -aoE "n_ctx_slot = [0-9]+|n_threads = [0-9]+|kv_unified = '[a-z]+'|loading model '[^']*'" "$LOG" | sort -u
curl -s "http://127.0.0.1:${PORT}/tokenize" -H 'Content-Type: application/json' \
  -d '{"content":"Ternary-Bonsai-2-27B-PQ2_0"}' | head -c 120; echo

# One request. NOTE: this model is a thinking model, so a short max_tokens legitimately
# returns content:"" with the whole budget spent on reasoning - that is not a failure,
# but it IS why the eval sets max_output_tokens well above the answer length.
curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"Name the capital of France. One word."}],"max_tokens":96,"temperature":0}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
ch = (d.get('choices') or [{}])[0]
m  = ch.get('message') or {}
t  = d.get('timings') or {}
content = (m.get('content') or '').strip()
reason  = (m.get('reasoning_content') or '').strip()
print('liveness: finish=%s content=%r reasoning_len=%d tokens=%s' %
      (ch.get('finish_reason'), content[:40], len(reason), (d.get('usage') or {}).get('completion_tokens')))
if not content and not reason:
    sys.exit('LIVENESS FAILED: no content and no reasoning - server is not really serving')
"

echo "ready on http://127.0.0.1:${PORT}/v1  (kill $SRV to stop)"
wait $SRV
