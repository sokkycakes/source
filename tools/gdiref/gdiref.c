/*
 * gdiref: dump what Windows GDI produces for a grid of fonts, sizes,
 * weights and quality settings, so other rasterizers can be compared
 * against it pixel for pixel. Black-box test tool; not engine code.
 *
 * Output (in <outdir>):
 *   env.json            OS version and font-smoothing settings
 *   fonts/<face>_<w>.ttf  the font file GDI actually used (GetFontData)
 *   glyphs.jsonl        one line per (config, codepoint)
 *   configs.jsonl       one line per config: realised face and metrics
 *
 * Build: x86_64-w64-mingw32-gcc -O2 -municode -o gdiref.exe gdiref.c -lgdi32
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static FILE *g_glyphs, *g_configs;

static const char b64tab[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static void put_b64(FILE *f, const unsigned char *p, size_t n)
{
	size_t i;
	for (i = 0; i + 2 < n; i += 3) {
		unsigned v = (p[i] << 16) | (p[i+1] << 8) | p[i+2];
		fputc(b64tab[v >> 18], f); fputc(b64tab[(v >> 12) & 63], f);
		fputc(b64tab[(v >> 6) & 63], f); fputc(b64tab[v & 63], f);
	}
	if (n - i == 1) {
		unsigned v = p[i] << 16;
		fputc(b64tab[v >> 18], f); fputc(b64tab[(v >> 12) & 63], f); fputs("==", f);
	} else if (n - i == 2) {
		unsigned v = (p[i] << 16) | (p[i+1] << 8);
		fputc(b64tab[v >> 18], f); fputc(b64tab[(v >> 12) & 63], f);
		fputc(b64tab[(v >> 6) & 63], f); fputc('=', f);
	}
}

static void put_table_size(FILE *f, HDC dc, const char *tag)
{
	DWORD t = tag[0] | (tag[1] << 8) | (tag[2] << 16) | ((DWORD)tag[3] << 24);
	DWORD n = GetFontData(dc, t, 0, NULL, 0);
	fprintf(f, "\"%s\":%ld", tag, n == GDI_ERROR ? -1L : (long)n);
}

static const char *g_faces[] = { "Tahoma", "Verdana", "Arial", "Courier New", "Trebuchet MS", "Times New Roman" };
static const int g_weights[] = { 400, 700 };

static void codepoints(int *out, int *count)
{
	int n = 0, c;
	for (c = 0x20; c <= 0x7E; c++) out[n++] = c;
	for (c = 0xA0; c <= 0xFF; c++) out[n++] = c;
	for (c = 0x410; c <= 0x44F; c++) out[n++] = c;
	out[n++] = 0x0401; out[n++] = 0x0451; out[n++] = 0x2014; out[n++] = 0x2026;
	out[n++] = 0x20AC; out[n++] = 0x2122;
	*count = n;
}

static void dump_font_file(HDC dc, const char *face, int weight, const char *outdir)
{
	char path[MAX_PATH];
	DWORD n = GetFontData(dc, 0, 0, NULL, 0);
	unsigned char *buf;
	FILE *f;
	if (n == GDI_ERROR || n == 0) {
		/* TrueType collections need the 'ttcf' tag */
		n = GetFontData(dc, 0x66637474, 0, NULL, 0);
		if (n == GDI_ERROR || n == 0) return;
		buf = malloc(n);
		GetFontData(dc, 0x66637474, 0, buf, n);
		snprintf(path, sizeof(path), "%s\\fonts\\%s_%d.ttc", outdir, face, weight);
	} else {
		buf = malloc(n);
		GetFontData(dc, 0, 0, buf, n);
		snprintf(path, sizeof(path), "%s\\fonts\\%s_%d.ttf", outdir, face, weight);
	}
	f = fopen(path, "wb");
	if (f) { fwrite(buf, 1, n, f); fclose(f); }
	free(buf);
}

static void run_config(const char *face, int tall, int weight, int aa, const int *cps, int ncp, const char *outdir, int dumpfont)
{
	HDC dc = CreateCompatibleDC(NULL);
	HFONT font = CreateFontA(tall, 0, 0, 0, weight, 0, 0, 0, ANSI_CHARSET,
		OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
		aa ? ANTIALIASED_QUALITY : NONANTIALIASED_QUALITY,
		DEFAULT_PITCH | FF_DONTCARE, face);
	TEXTMETRICW tm;
	char realface[LF_FACESIZE * 2] = { 0 };
	WCHAR wface[LF_FACESIZE];
	OUTLINETEXTMETRICW *otm = NULL;
	UINT otmsize;
	BITMAPINFOHEADER bih;
	unsigned char *bits = NULL;
	HBITMAP dib;
	int dibw, dibh, pad = 8, i;
	MAT2 mat = { {0, 1}, {0, 0}, {0, 0}, {0, 1} };

	SelectObject(dc, font);
	SetMapMode(dc, MM_TEXT);
	GetTextMetricsW(dc, &tm);
	GetTextFaceW(dc, LF_FACESIZE, wface);
	WideCharToMultiByte(CP_UTF8, 0, wface, -1, realface, sizeof(realface), NULL, NULL);

	if (dumpfont) dump_font_file(dc, face, weight, outdir);

	fprintf(g_configs, "{\"face\":\"%s\",\"tall\":%d,\"weight\":%d,\"aa\":%d,\"realface\":\"%s\","
		"\"tmHeight\":%ld,\"tmAscent\":%ld,\"tmDescent\":%ld,\"tmInternalLeading\":%ld,\"tmExternalLeading\":%ld,"
		"\"tmAveCharWidth\":%ld,\"tmMaxCharWidth\":%ld,\"tmWeight\":%ld,\"tmOverhang\":%ld,",
		face, tall, weight, aa, realface, tm.tmHeight, tm.tmAscent, tm.tmDescent, tm.tmInternalLeading,
		tm.tmExternalLeading, tm.tmAveCharWidth, tm.tmMaxCharWidth, tm.tmWeight, tm.tmOverhang);
	otmsize = GetOutlineTextMetricsW(dc, 0, NULL);
	if (otmsize) {
		otm = malloc(otmsize);
		GetOutlineTextMetricsW(dc, otmsize, otm);
		fprintf(g_configs, "\"otmEMSquare\":%u,\"otmAscent\":%d,\"otmDescent\":%d,\"otmLineGap\":%u,"
			"\"otmsUnderscoreSize\":%d,\"otmsUnderscorePosition\":%d,\"otmsStrikeoutSize\":%u,\"otmsStrikeoutPosition\":%d,",
			otm->otmEMSquare, otm->otmAscent, otm->otmDescent, otm->otmLineGap,
			otm->otmsUnderscoreSize, otm->otmsUnderscorePosition, otm->otmsStrikeoutSize, otm->otmsStrikeoutPosition);
		free(otm);
	}
	fputs("\"tables\":{", g_configs);
	put_table_size(g_configs, dc, "gasp"); fputc(',', g_configs);
	put_table_size(g_configs, dc, "EBLC"); fputc(',', g_configs);
	put_table_size(g_configs, dc, "EBDT"); fputc(',', g_configs);
	put_table_size(g_configs, dc, "hdmx"); fputc(',', g_configs);
	put_table_size(g_configs, dc, "LTSH"); fputc(',', g_configs);
	put_table_size(g_configs, dc, "VDMX"); fputc(',', g_configs);
	put_table_size(g_configs, dc, "fpgm");
	fputs("}}\n", g_configs);

	dibw = tm.tmMaxCharWidth * 2 + pad * 2;
	dibh = tm.tmHeight + pad * 2;
	memset(&bih, 0, sizeof(bih));
	bih.biSize = sizeof(bih);
	bih.biWidth = dibw;
	bih.biHeight = -dibh;
	bih.biPlanes = 1;
	bih.biBitCount = 32;
	bih.biCompression = BI_RGB;
	dib = CreateDIBSection(dc, (BITMAPINFO *)&bih, DIB_RGB_COLORS, (void **)&bits, NULL, 0);
	SelectObject(dc, dib);
	SetTextAlign(dc, TA_LEFT | TA_TOP | TA_NOUPDATECP);

	for (i = 0; i < ncp; i++) {
		WCHAR ch = (WCHAR)cps[i];
		ABC abc = { 0, 0, 0 };
		GLYPHMETRICS gm;
		DWORD n;
		unsigned char *buf;
		int abcok = GetCharABCWidthsW(dc, ch, ch, &abc);
		int x, y, minx = dibw, miny = dibh, maxx = -1, maxy = -1, colour = 0;
		RECT rc = { 0, 0, dibw, dibh };

		fprintf(g_glyphs, "{\"face\":\"%s\",\"tall\":%d,\"weight\":%d,\"aa\":%d,\"cp\":%d,\"abcok\":%d,\"a\":%d,\"b\":%u,\"c\":%d",
			face, tall, weight, aa, cps[i], abcok, abc.abcA, abc.abcB, abc.abcC);

		/* GGO_GRAY8_BITMAP: 0..64 coverage, DWORD-aligned rows */
		memset(&gm, 0, sizeof(gm));
		n = GetGlyphOutlineW(dc, ch, GGO_GRAY8_BITMAP, &gm, 0, NULL, &mat);
		fprintf(g_glyphs, ",\"gray8\":{\"ret\":%ld,\"bbx\":%u,\"bby\":%u,\"ox\":%ld,\"oy\":%ld,\"incx\":%d,\"incy\":%d",
			n == GDI_ERROR ? -1L : (long)n, gm.gmBlackBoxX, gm.gmBlackBoxY, gm.gmptGlyphOrigin.x, gm.gmptGlyphOrigin.y,
			gm.gmCellIncX, gm.gmCellIncY);
		if (n != GDI_ERROR && n > 0) {
			buf = malloc(n);
			GetGlyphOutlineW(dc, ch, GGO_GRAY8_BITMAP, &gm, n, buf, &mat);
			fputs(",\"data\":\"", g_glyphs); put_b64(g_glyphs, buf, n); fputc('"', g_glyphs);
			free(buf);
		}
		fputc('}', g_glyphs);

		/* GGO_BITMAP: 1bpp, DWORD-aligned rows */
		memset(&gm, 0, sizeof(gm));
		n = GetGlyphOutlineW(dc, ch, GGO_BITMAP, &gm, 0, NULL, &mat);
		fprintf(g_glyphs, ",\"mono\":{\"ret\":%ld,\"bbx\":%u,\"bby\":%u,\"ox\":%ld,\"oy\":%ld",
			n == GDI_ERROR ? -1L : (long)n, gm.gmBlackBoxX, gm.gmBlackBoxY, gm.gmptGlyphOrigin.x, gm.gmptGlyphOrigin.y);
		if (n != GDI_ERROR && n > 0) {
			buf = malloc(n);
			GetGlyphOutlineW(dc, ch, GGO_BITMAP, &gm, n, buf, &mat);
			fputs(",\"data\":\"", g_glyphs); put_b64(g_glyphs, buf, n); fputc('"', g_glyphs);
			free(buf);
		}
		fputc('}', g_glyphs);

		/* ExtTextOutW, white on opaque black, pen origin at (pad - a, pad) so
		   the black box starts at column pad. Cropped to the inked area. */
		SetBkColor(dc, RGB(0, 0, 0));
		SetTextColor(dc, RGB(255, 255, 255));
		SetBkMode(dc, OPAQUE);
		ExtTextOutW(dc, 0, 0, ETO_OPAQUE, &rc, NULL, 0, NULL);
		ExtTextOutW(dc, pad - abc.abcA, pad, 0, NULL, &ch, 1, NULL);
		GdiFlush();
		for (y = 0; y < dibh; y++)
			for (x = 0; x < dibw; x++) {
				unsigned char *p = bits + (y * dibw + x) * 4;
				if (p[0] | p[1] | p[2]) {
					if (x < minx) minx = x;
					if (x > maxx) maxx = x;
					if (y < miny) miny = y;
					if (y > maxy) maxy = y;
					if (p[0] != p[1] || p[1] != p[2]) colour = 1;
				}
			}
		fprintf(g_glyphs, ",\"eto\":{\"pad\":%d,\"colour\":%d", pad, colour);
		if (maxx >= 0) {
			int w = maxx - minx + 1, h = maxy - miny + 1;
			unsigned char *crop = malloc(w * h * 3), *q = crop;
			for (y = miny; y <= maxy; y++)
				for (x = minx; x <= maxx; x++) {
					unsigned char *p = bits + (y * dibw + x) * 4;
					*q++ = p[2]; *q++ = p[1]; *q++ = p[0]; /* RGB */
				}
			fprintf(g_glyphs, ",\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"rgb\":\"", minx - pad, miny - pad, w, h);
			put_b64(g_glyphs, crop, w * h * 3);
			fputc('"', g_glyphs);
			free(crop);
		}
		fputs("}}\n", g_glyphs);
	}

	DeleteObject(dib);
	DeleteObject(font);
	DeleteDC(dc);
}

typedef LONG (WINAPI *RtlGetVersion_t)(OSVERSIONINFOW *);

int wmain(int argc, WCHAR **argv)
{
	char outdir[MAX_PATH] = ".", path[MAX_PATH];
	int cps[512], ncp, f, w, t, aa;
	UINT smoothing = 0, smoothtype = 0, contrast = 0;
	OSVERSIONINFOW ver = { sizeof(ver) };
	RtlGetVersion_t rgv = (RtlGetVersion_t)GetProcAddress(GetModuleHandleA("ntdll.dll"), "RtlGetVersion");
	HMODULE wine = GetModuleHandleA("ntdll.dll");
	const char *(CDECL *wine_get_version)(void) = wine ? (void *)GetProcAddress(wine, "wine_get_version") : NULL;
	FILE *env;

	if (argc > 1) WideCharToMultiByte(CP_UTF8, 0, argv[1], -1, outdir, sizeof(outdir), NULL, NULL);
	CreateDirectoryA(outdir, NULL);
	snprintf(path, sizeof(path), "%s\\fonts", outdir);
	CreateDirectoryA(path, NULL);

	if (rgv) rgv(&ver);
	SystemParametersInfoW(SPI_GETFONTSMOOTHING, 0, &smoothing, 0);
	SystemParametersInfoW(SPI_GETFONTSMOOTHINGTYPE, 0, &smoothtype, 0);
	SystemParametersInfoW(SPI_GETFONTSMOOTHINGCONTRAST, 0, &contrast, 0);
	snprintf(path, sizeof(path), "%s\\env.json", outdir);
	env = fopen(path, "w");
	fprintf(env, "{\"os\":\"%lu.%lu.%lu\",\"wine\":\"%s\",\"fontsmoothing\":%u,\"smoothingtype\":%u,\"contrast\":%u}\n",
		ver.dwMajorVersion, ver.dwMinorVersion, ver.dwBuildNumber,
		wine_get_version ? wine_get_version() : "", smoothing, smoothtype, contrast);
	fclose(env);

	snprintf(path, sizeof(path), "%s\\glyphs.jsonl", outdir);
	g_glyphs = fopen(path, "w");
	snprintf(path, sizeof(path), "%s\\configs.jsonl", outdir);
	g_configs = fopen(path, "w");
	codepoints(cps, &ncp);

	for (f = 0; f < (int)(sizeof(g_faces) / sizeof(g_faces[0])); f++)
		for (w = 0; w < 2; w++)
			for (t = 8; t <= 24; t++)
				for (aa = 0; aa <= 1; aa++)
					run_config(g_faces[f], t, g_weights[w], aa, cps, ncp, outdir, t == 8 && aa == 0);

	fclose(g_glyphs);
	fclose(g_configs);
	return 0;
}
