#!/usr/bin/env python3
"""Print 1:1 greyscale PNG text strips from two gdiref captures as base64,
so they can be lifted out of a CI log.  Each line: SHEET <label> <base64>

usage: sheets.py <ref_dir> <cand_dir>
"""
import base64, json, os, struct, sys, zlib

TEXTS = ["The quick brown fox jumps over the lazy dog 0123456789",
         "Player: gg wp! (100%) Привет, как дела? éàü"]
CONFIGS = [(f, t, w, aa) for f in ("Tahoma", "Verdana", "Arial") for t in (11, 13, 16) for w in (400, 700) for aa in (0, 1)]

def load(d):
    cfg, gl = {}, {}
    for line in open(os.path.join(d, "configs.jsonl"), encoding="utf-8"):
        c = json.loads(line); cfg[(c["face"], c["tall"], c["weight"], c["aa"])] = c
    for line in open(os.path.join(d, "glyphs.jsonl"), encoding="utf-8"):
        g = json.loads(line); gl[(g["face"], g["tall"], g["weight"], g["aa"], g["cp"])] = g
    return cfg, gl

def strip(cfg, gl, key, text):
    c = cfg[key]
    H = c["tmHeight"] + 2
    pen, pieces = 1, []
    for ch in text:
        g = gl.get(key + (ord(ch),))
        if not g:
            continue
        e = g["eto"]
        if "rgb" in e:
            pieces.append((pen + g["a"] + e["x"], 1 + e["y"], e["w"], e["h"], base64.b64decode(e["rgb"])))
        pen += g["a"] + g["b"] + g["c"]
    W = pen + 2
    img = [[0] * W for _ in range(H)]
    for x0, y0, w, h, px in pieces:
        for y in range(h):
            for x in range(w):
                if 0 <= y0 + y < H and 0 <= x0 + x < W:
                    img[y0 + y][x0 + x] = max(img[y0 + y][x0 + x], px[(y * w + x) * 3])
    return img

def png(img):
    H, W = len(img), len(img[0])
    raw = b"".join(b"\0" + bytes(r) for r in img)
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 0, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw, 9)) + ch(b"IEND", b"")

ref, cand = load(sys.argv[1]), load(sys.argv[2])
for key in CONFIGS:
    for ti, text in enumerate(TEXTS):
        for side, (cfg, gl) in (("windows", ref), ("emulation", cand)):
            if key in cfg:
                label = f"{side}|{key[0]}|{key[1]}|{key[2]}|{key[3]}|{ti}"
                print("SHEET", label, base64.b64encode(png(strip(cfg, gl, key, text))).decode())
