"""NEGATIVE CONTROL F2 (visible-green): right separators, right length rule, wrong fold.

Tokenizes with [^\\W_]+ (so words_edge.txt stays green) but normalizes with
lower() where the prompt says casefold -> caught by casefold-expansion-and-ties.
"""
import argparse, csv, re
from collections import Counter
from pathlib import Path

ap = argparse.ArgumentParser()
for f in ("--input", "--stopwords", "--out"):
    ap.add_argument(f, required=True)
a = ap.parse_args()

text = Path(a.input).read_text(encoding="utf-8")
sw = Path(a.stopwords).read_text(encoding="utf-8").splitlines()
stops = {line.strip().lower() for line in sw if line.strip()}
counts = Counter()
for tok in re.findall(r"[^\W_]+", text):
    w = tok.lower()
    if len(w) < 2 or w in stops:
        continue
    counts[w] += 1

Path(a.out).parent.mkdir(parents=True, exist_ok=True)
with open(a.out, "w", encoding="utf-8", newline="") as fh:
    wr = csv.writer(fh, lineterminator="\n")
    wr.writerow(["word", "count"])
    for w, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        wr.writerow([w, str(c)])
