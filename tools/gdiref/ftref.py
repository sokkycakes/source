#!/usr/bin/env python3
"""Produce gdiref-format output with FreeType, configured to approximate GDI.

usage: ftref.py <fonts_dir_from_windows_capture> <outdir>

Settings (each is a guess under test, not established GDI behaviour):
  * size: if the font has a VDMX table, pick the largest ppem whose
    yMax - yMin fits in the requested cell height; otherwise
    ppem = round(tall * unitsPerEm / (usWinAscent + usWinDescent)).
    Ascent/descent come from VDMX, else winAscent/winDescent scaled.
  * hinting: TrueType bytecode, interpreter v35 (set via FREETYPE_PROPERTIES).
  * NONANTIALIASED_QUALITY: FT mono render.
  * ANTIALIASED_QUALITY: grey only when the gasp range for this ppem has the
    GASP_DOGRAY bit, quantised to 17 levels; otherwise mono.
  * GGO_GRAY8_BITMAP: FT grey render quantised to 0..64. GGO_BITMAP: mono.
"""
import os, sys
INTERP = os.environ.get("FTREF_INTERP", "35")
os.environ["FREETYPE_PROPERTIES"] = "truetype:interpreter-version=" + INTERP
import base64, json, struct
import freetype

FACES = ["Tahoma", "Verdana", "Arial", "Courier New", "Trebuchet MS", "Times New Roman"]

def codepoints():
    cps = list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + list(range(0x410, 0x450))
    return cps + [0x401, 0x451, 0x2014, 0x2026, 0x20AC, 0x2122]

def sfnt_tables(data):
    n = struct.unpack(">H", data[4:6])[0]
    t = {}
    for i in range(n):
        tag, _, off, ln = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        t[tag.decode("latin1")] = data[off:off + ln]
    return t

def parse_vdmx(v):
    """Return {ppem: (yMax, yMin)} from the first 1:1-ish ratio group."""
    if not v:
        return {}
    _, numRecs, numRatios = struct.unpack(">HHH", v[:6])
    ratios = [struct.unpack(">BBBB", v[6 + 4 * i:10 + 4 * i]) for i in range(numRatios)]
    offs = [struct.unpack(">H", v[6 + 4 * numRatios + 2 * i:8 + 4 * numRatios + 2 * i])[0] for i in range(numRatios)]
    pick = None
    for (cs, x, y0, y1), off in zip(ratios, offs):
        if x == 0 or (y0 <= x <= y1) or (x == 1 and y0 <= 1 <= y1):
            pick = off
            break
    if pick is None:
        return {}
    recs, startsz, endsz = struct.unpack(">HBB", v[pick:pick + 4])
    out = {}
    for i in range(recs):
        ppem, ymax, ymin = struct.unpack(">Hhh", v[pick + 4 + 6 * i:pick + 10 + 6 * i])
        out[ppem] = (ymax, ymin)
    return out

def parse_gasp(g):
    if not g:
        return []
    _, n = struct.unpack(">HH", g[:4])
    return [struct.unpack(">HH", g[4 + 4 * i:8 + 4 * i]) for i in range(n)]

def gasp_gray(ranges, ppem):
    for maxppem, flags in ranges:
        if ppem <= maxppem:
            return bool(flags & 2)
    return True

def b64(b):
    return base64.b64encode(bytes(b)).decode()

def render(face, ch, mono):
    flags = freetype.FT_LOAD_RENDER | (freetype.FT_LOAD_TARGET_MONO | freetype.FT_LOAD_MONOCHROME if mono else freetype.FT_LOAD_TARGET_NORMAL)
    face.load_char(ch, flags)
    s = face.glyph
    bm = s.bitmap
    w, h, pitch = bm.width, bm.rows, bm.pitch
    buf = bm.buffer
    px = []
    for y in range(h):
        row = []
        for x in range(w):
            if bm.pixel_mode == freetype.FT_PIXEL_MODE_MONO:
                row.append(255 if buf[y * pitch + (x >> 3)] & (0x80 >> (x & 7)) else 0)
            else:
                row.append(buf[y * pitch + x])
        px.append(row)
    return s, w, h, px

def main():
    fdir, out = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(out, "fonts"), exist_ok=True)
    gl = open(os.path.join(out, "glyphs.jsonl"), "w")
    cf = open(os.path.join(out, "configs.jsonl"), "w")
    json.dump({"os": "freetype " + ".".join(map(str, freetype.version())), "wine": "", "fontsmoothing": 0, "smoothingtype": 0, "contrast": 0},
              open(os.path.join(out, "env.json"), "w"))
    fontinfo = []
    for name in FACES:
        for weight in (400, 700):
            path = os.path.join(fdir, f"{name}_{weight}.ttf")
            if not os.path.exists(path):
                continue
            data = open(path, "rb").read()
            tabs = sfnt_tables(data)
            os2 = tabs["OS/2"]
            winA, winD = struct.unpack(">HH", os2[74:78])
            upem = struct.unpack(">H", tabs["head"][18:20])[0]
            vdmx = parse_vdmx(tabs.get("VDMX"))
            gasp = parse_gasp(tabs.get("gasp"))
            hhea = tabs["hhea"]
            info = {"face": name, "weight": weight, "upem": upem, "winAscent": winA, "winDescent": winD,
                    "typo": struct.unpack(">hhh", os2[68:74]), "hhea": struct.unpack(">hhh", hhea[4:10]),
                    "gasp": gasp, "vdmx_ratios": [], "vdmx": {}}
            v = tabs.get("VDMX")
            if v:
                _, nrec, nrat = struct.unpack(">HHH", v[:6])
                info["vdmx_ratios"] = [struct.unpack(">BBBB", v[6 + 4 * i:10 + 4 * i]) for i in range(nrat)]
                info["vdmx"] = {str(k): val for k, val in sorted(vdmx.items()) if k <= 30}
            fontinfo.append(info)
            face = freetype.Face(path)
            for tall in range(8, 25):
                fits = [p for p, (a, d) in vdmx.items() if a - d <= tall] if vdmx else []
                if fits and os.environ.get("FTREF_NOVDMX") != "1":
                    ppem = max(fits)
                    asc, desc = vdmx.get(ppem, (0, 0))
                    desc = -desc
                else:
                    ppem = int(tall * upem / (winA + winD) + 0.5)
                    asc = int(winA * ppem / upem + 0.5)
                    desc = int(winD * ppem / upem + 0.5)
                face.set_pixel_sizes(0, ppem)
                for aa in (0, 1):
                    cf.write(json.dumps({"face": name, "tall": tall, "weight": weight, "aa": aa, "realface": face.family_name.decode(),
                                         "tmHeight": asc + desc, "tmAscent": asc, "tmDescent": desc, "tmInternalLeading": asc + desc - ppem,
                                         "tmExternalLeading": 0, "tmAveCharWidth": 0, "tmMaxCharWidth": int(face.size.max_advance) >> 6,
                                         "tmWeight": weight, "tmOverhang": 0, "ppem": ppem, "tables": {}}) + "\n")
                    eto_gray = aa == 1 and gasp_gray(gasp, ppem)
                    for cp in codepoints():
                        ch = chr(cp)
                        rec = {"face": name, "tall": tall, "weight": weight, "aa": aa, "cp": cp}
                        s, w, h, mpx = render(face, ch, True)
                        a = s.bitmap_left
                        adv = (s.advance.x + 32) >> 6
                        rec.update(abcok=1, a=a, b=max(w, 1) if w else 0, c=adv - a - w)
                        pitch = ((w + 31) // 32) * 4
                        mono = bytearray(pitch * h)
                        for y in range(h):
                            for x in range(w):
                                if mpx[y][x]:
                                    mono[y * pitch + (x >> 3)] |= 0x80 >> (x & 7)
                        rec["mono"] = {"ret": len(mono), "bbx": w, "bby": h, "ox": s.bitmap_left, "oy": s.bitmap_top}
                        if mono:
                            rec["mono"]["data"] = b64(mono)
                        s2, w2, h2, gpx = render(face, ch, False)
                        gp = (w2 + 3) & ~3
                        g8 = bytearray(gp * h2)
                        for y in range(h2):
                            for x in range(w2):
                                g8[y * gp + x] = (gpx[y][x] * 64 + 127) // 255
                        rec["gray8"] = {"ret": len(g8), "bbx": w2, "bby": h2, "ox": s2.bitmap_left, "oy": s2.bitmap_top,
                                        "incx": adv, "incy": 0}
                        if g8:
                            rec["gray8"]["data"] = b64(g8)
                        if eto_gray:
                            src, ox, oy, sw, sh = gpx, s2.bitmap_left, s2.bitmap_top, w2, h2
                            src = [[((v * 16 + 127) // 255) * 255 // 16 for v in row] for row in src]
                        else:
                            src, ox, oy, sw, sh = mpx, s.bitmap_left, s.bitmap_top, w, h
                        ys = [y for y in range(sh) if any(src[y])]
                        xs = [x for x in range(sw) if any(src[y][x] for y in range(sh))]
                        eto = {"pad": 8, "colour": 0}
                        if ys and xs:
                            x0, x1, y0, y1 = xs[0], xs[-1], ys[0], ys[-1]
                            rgb = bytearray()
                            for y in range(y0, y1 + 1):
                                for x in range(x0, x1 + 1):
                                    rgb += bytes((src[y][x],) * 3)
                            eto.update(x=ox + x0 - a, y=asc - oy + y0, w=x1 - x0 + 1, h=y1 - y0 + 1, rgb=b64(rgb))
                        rec["eto"] = eto
                        gl.write(json.dumps(rec) + "\n")
    gl.close()
    cf.close()
    json.dump(fontinfo, open(os.path.join(out, "fontinfo.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
