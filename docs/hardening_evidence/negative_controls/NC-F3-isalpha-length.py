"""NEGATIVE CONTROL F3 (visible-green): casefold, but the length filter is isalpha()-qualified.

words.txt / words_edge.txt never hold a one-character digit token, so the visible
pair stays green; the bare `1` in separators-and-digit-length survives.
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
stops = {line.strip().casefold() for line in sw if line.strip()}
counts = Counter()
for tok in re.findall(r"[^\W_]+", text):
    w = tok.casefold()
    if w in stops or (w.isalpha() and len(w) < 2):
        continue
    counts[w] += 1

Path(a.out).parent.mkdir(parents=True, exist_ok=True)
with open(a.out, "w", encoding="utf-8", newline="") as fh:
    wr = csv.writer(fh, lineterminator="\n")
    wr.writerow(["word", "count"])
    for w, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        wr.writerow([w, str(c)])
