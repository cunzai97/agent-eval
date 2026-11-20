"""NEGATIVE CONTROL F4 (visible-green): stopword lines are re-tokenized, not read whole.

stopwords.txt holds the single word `stop`, so both readings agree on the visible
inputs; the line `beta-gamma` in stopword-lines-are-whole-lines becomes two
stopwords there and silently deletes beta and gamma.
"""
import argparse, csv, re
from collections import Counter
from pathlib import Path

ap = argparse.ArgumentParser()
for f in ("--input", "--stopwords", "--out"):
    ap.add_argument(f, required=True)
a = ap.parse_args()

text = Path(a.input).read_text(encoding="utf-8")
sw = Path(a.stopwords).read_text(encoding="utf-8")
stops = {tok.casefold() for tok in re.findall(r"[^\W_]+", sw)}
counts = Counter()
for tok in re.findall(r"[^\W_]+", text):
    w = tok.casefold()
    if len(w) < 2 or w in stops:
        continue
    counts[w] += 1

Path(a.out).parent.mkdir(parents=True, exist_ok=True)
with open(a.out, "w", encoding="utf-8", newline="") as fh:
    wr = csv.writer(fh, lineterminator="\n")
    wr.writerow(["word", "count"])
    for w, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        wr.writerow([w, str(c)])
