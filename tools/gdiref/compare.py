#!/usr/bin/env python3
"""Compare two gdiref output folders (e.g. Windows vs Wine) and summarise.

usage: compare.py <reference_dir> <candidate_dir> [--md out.md] [--sheets outdir]
Either dir may be given alone with --facts to only summarise one capture.
"""
import base64, hashlib, json, os, struct, sys, zlib
from collections import defaultdict

def load(d):
    cfg = {}
    with open(os.path.join(d, "configs.jsonl"), encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            cfg[(c["face"], c["tall"], c["weight"], c["aa"])] = c
    gl = {}
    with open(os.path.join(d, "glyphs.jsonl"), encoding="utf-8") as f:
        for line in f:
            g = json.loads(line)
            gl[(g["face"], g["tall"], g["weight"], g["aa"], g["cp"])] = g
    env = json.load(open(os.path.join(d, "env.json")))
    return env, cfg, gl

def eto_levels(g):
    e = g["eto"]
    if "rgb" not in e:
        return set()
    px = base64.b64decode(e["rgb"])
    return set(px[0::3])

def png(path, w, h, rows):
    raw = b"".join(b"\0" + bytes(r) for r in rows)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

def sheet(gl, cfg, face, tall, weight, aa, text, path, scale=3):
    """Draw text with ExtTextOut glyph crops at their ABC advances, grey only (R channel)."""
    c = cfg.get((face, tall, weight, aa))
    if not c:
        return
    H = c["tmHeight"] + 4
    pen, pieces = 2, []
    for ch in text:
        g = gl.get((face, tall, weight, aa, ord(ch)))
        if not g:
            continue
        e = g["eto"]
        if "rgb" in e:
            pieces.append((pen + g["a"] + e["x"], 2 + e["y"], e["w"], e["h"], base64.b64decode(e["rgb"])))
        pen += g["a"] + g["b"] + g["c"]
    W = pen + 4
    img = [[0] * W for _ in range(H)]
    for x0, y0, w, h, px in pieces:
        for y in range(h):
            for x in range(w):
                if 0 <= y0 + y < H and 0 <= x0 + x < W:
                    img[y0 + y][x0 + x] = max(img[y0 + y][x0 + x], px[(y * w + x) * 3])
    rows = [[v for v in row for _ in range(scale)] for row in img for _ in range(scale)]
    png(path, W * scale, H * scale, rows)

def facts(name, env, cfg, gl, out):
    out.append(f"## {name}: environment\n\n`{json.dumps(env)}`\n")
    out.append(f"## {name}: what the quality flag produces (ExtTextOutW, printable ASCII)\n")
    out.append("| face | weight | tall | aa=0 levels | aa=1 levels | aa=1 colour fringes | gasp | EBLC |")
    out.append("|---|---|---|---|---|---|---|---|")
    for (face, tall, weight, aa), c in sorted(cfg.items()):
        if aa != 0:
            continue
        lv = {}
        col = 0
        for a in (0, 1):
            s = set()
            for cp in range(0x21, 0x7f):
                g = gl.get((face, tall, weight, a, cp))
                if g:
                    s |= eto_levels(g)
                    if a == 1:
                        col |= g["eto"]["colour"]
            lv[a] = len(s)
        t = c.get("tables", {})
        out.append(f"| {face} ({c['realface']}) | {weight} | {tall} | {lv[0]} | {lv[1]} | {col} | {t.get('gasp')} | {t.get('EBLC')} |")
    out.append("")

def main():
    args = sys.argv[1:]
    md = None
    sheets = None
    if "--md" in args:
        i = args.index("--md"); md = args[i + 1]; del args[i:i + 2]
    if "--sheets" in args:
        i = args.index("--sheets"); sheets = args[i + 1]; del args[i:i + 2]
    out = []
    caps = [(os.path.basename(os.path.normpath(d)), *load(d)) for d in args]
    for name, env, cfg, gl in caps:
        facts(name, env, cfg, gl, out)
        if sheets:
            os.makedirs(sheets, exist_ok=True)
            for face in ("Tahoma", "Verdana", "Arial"):
                for tall in (11, 13, 16, 20):
                    for aa in (0, 1):
                        sheet(gl, cfg, face, tall, 400, aa, "The quick brown fox 0123 Ж", os.path.join(sheets, f"{name}_{face}_{tall}_aa{aa}.png"))
    if len(caps) == 2:
        (rn, renv, rcfg, rgl), (cn, cenv, ccfg, cgl) = caps
        out.append(f"## Font files each side actually used (sha1 of GetFontData)\n")
        out.append("| file | " + rn + " | " + cn + " |\n|---|---|---|")
        def h(d, n):
            p = os.path.join(d, "fonts", n)
            return hashlib.sha1(open(p, "rb").read()).hexdigest()[:12] if os.path.exists(p) else "missing"
        names = sorted(set(os.listdir(os.path.join(args[0], "fonts"))) | set(os.listdir(os.path.join(args[1], "fonts"))))
        for n in names:
            out.append(f"| {n} | {h(args[0], n)} | {h(args[1], n)} |")
        out.append("")
        out.append(f"## {cn} vs {rn}: identical fraction per face and quality\n")
        out.append("| face | weight | aa | configs | tm metrics equal | ABC equal | GGO gray8 equal | GGO mono equal | ExtTextOut equal |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        agg = defaultdict(lambda: [0] * 6)
        for k, rc in rcfg.items():
            cc = ccfg.get(k)
            if not cc:
                continue
            face, tall, weight, aa = k
            a = agg[(face, weight, aa)]
            a[0] += 1
            a[1] += all(rc[m] == cc[m] for m in ("tmHeight", "tmAscent", "tmDescent", "tmMaxCharWidth"))
        gagg = defaultdict(lambda: [0, 0, 0, 0, 0])
        for k, rg in rgl.items():
            cg = cgl.get(k)
            if not cg:
                continue
            face, tall, weight, aa, cp = k
            s = gagg[(face, weight, aa)]
            s[0] += 1
            s[1] += (rg["a"], rg["b"], rg["c"]) == (cg["a"], cg["b"], cg["c"])
            s[2] += rg["gray8"] == cg["gray8"]
            s[3] += rg["mono"] == cg["mono"]
            s[4] += rg["eto"] == cg["eto"]
        for key in sorted(agg):
            a, s = agg[key], gagg[key]
            n = max(s[0], 1)
            out.append(f"| {key[0]} | {key[1]} | {key[2]} | {a[0]} | {a[1]}/{a[0]} | {100*s[1]/n:.1f}% | {100*s[2]/n:.1f}% | {100*s[3]/n:.1f}% | {100*s[4]/n:.1f}% |")
        out.append("")
        out.append("### First metric differences\n")
        shown = 0
        for k in sorted(rcfg):
            cc = ccfg.get(k)
            if cc and shown < 40:
                d = {m: (rcfg[k][m], cc[m]) for m in ("tmHeight", "tmAscent", "tmDescent", "tmMaxCharWidth", "tmAveCharWidth") if rcfg[k][m] != cc[m]}
                if d:
                    out.append(f"- {k}: " + ", ".join(f"{m} {a}→{b}" for m, (a, b) in d.items()))
                    shown += 1
        out.append("")
    text = "\n".join(out)
    if md:
        open(md, "w", encoding="utf-8").write(text)
    print(text)

if __name__ == "__main__":
    main()
