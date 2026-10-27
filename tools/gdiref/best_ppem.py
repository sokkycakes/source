#!/usr/bin/env python3
"""For each (face, weight, tall), count identical 1-bit glyphs between the
Windows capture and FreeType runs made with different ppem offsets.

usage: best_ppem.py <windows_dir> <label>=<ft_dir> [...]
"""
import json, sys
from collections import defaultdict

def mono(d):
    out = {}
    for line in open(f"{d}/glyphs.jsonl", encoding="utf-8"):
        g = json.loads(line)
        if g["aa"] == 0:
            m = g["mono"]
            out[(g["face"], g["weight"], g["tall"], g["cp"])] = (m.get("data"), m["ox"], m["oy"])
    return out

ref = mono(sys.argv[1])
runs = [(a.split("=", 1)[0], mono(a.split("=", 1)[1])) for a in sys.argv[2:]]
score = defaultdict(lambda: defaultdict(int))
total = defaultdict(int)
for k, v in ref.items():
    total[k[:3]] += 1
    for label, r in runs:
        score[k[:3]][label] += r.get(k) == v
print("face weight tall n " + " ".join(l for l, _ in runs))
for key in sorted(total):
    print(*key, total[key], *(score[key][l] for l, _ in runs))
