"""NEGATIVE CONTROL F1, named by the brief: .lower() plus a plain \w+ regex.

`\w+` keeps the underscore and lower() is not casefold(), so this one is
visible-RED as well (words_edge.txt) -- kept as the coarsest control.
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
for tok in re.findall(r"\w+", text):
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
