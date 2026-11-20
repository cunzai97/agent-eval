"""Prompt-literal reference implementation of script-002 freq.py (scratch, out of tree).

Rules asserted by tasks/script-dev/script-dev-002.yaml task_prompt:
  * tokenize on runs of Unicode letters or digits (hyphen / underscore / punctuation are separators)
  * casefold every token
  * drop stopwords and tokens shorter than 2 characters
  * print word,count sorted by count desc then word asc
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path


def tokenize(text: str) -> list[str]:
    """Runs of Unicode letters/digits; everything else separates."""
    toks: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ch.isalpha() or ch.isdigit():
            cur.append(ch)
        elif cur:
            toks.append("".join(cur))
            cur = []
    if cur:
        toks.append("".join(cur))
    return toks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--stopwords", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    stop_text = Path(args.stopwords).read_text(encoding="utf-8")
    stops = {line.strip().casefold() for line in stop_text.splitlines() if line.strip()}

    counts: Counter[str] = Counter()
    for tok in tokenize(text):
        word = tok.casefold()
        if len(word) < 2 or word in stops:
            continue
        counts[word] += 1

    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["word", "count"])
        for word, count in rows:
            writer.writerow([word, str(count)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
