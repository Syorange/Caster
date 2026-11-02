#!/usr/bin/env python3
"""Original 3000x3000 RGB JPEG cover (Apple Podcasts: 1400-3000 px square, RGB, JPEG/PNG) drawn with PIL.
usage: cover.py SHOW_DIR [--out path.jpg]
Uses show.json title + cover.tagline; palette/motif default to the music style
(news=chart, calm=waves, upbeat-tech=grid, warm-story=sun), override with cover.palette {bg_top,bg_bottom,accent,text}
and cover.motif (chart|waves|grid|sun|bars). Fonts: Noto Sans CJK (covers Latin + CJK), DejaVu fallback."""
import argparse, math, os, re, sys
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pk
W = 3000
PALETTES = {
    'news': dict(bg_top=(12, 22, 38), bg_bottom=(20, 62, 80), accent=(240, 186, 70), text=(255, 255, 255), motif='chart'),
    'calm': dict(bg_top=(28, 30, 66), bg_bottom=(108, 96, 160), accent=(250, 200, 170), text=(255, 255, 255), motif='waves'),
    'upbeat-tech': dict(bg_top=(10, 10, 20), bg_bottom=(70, 20, 120), accent=(60, 230, 230), text=(255, 255, 255), motif='grid'),
    'warm-story': dict(bg_top=(60, 32, 22), bg_bottom=(176, 88, 56), accent=(250, 226, 180), text=(255, 248, 235), motif='sun'),
    'none': dict(bg_top=(30, 30, 34), bg_bottom=(70, 74, 84), accent=(235, 235, 235), text=(255, 255, 255), motif='bars'),
}
CJK = re.compile(r'[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]')
NOTO = '/usr/share/fonts/opentype/noto/NotoSansCJK-{}.ttc'
DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf'

def font(lang, size, bold=True):
    region = {'zh-tw': 'TC', 'zh-hk': 'HK', 'ja': 'JP', 'ko': 'KR'}
    want = next((v for k, v in region.items() if lang.lower().startswith(k)), 'SC')
    p = NOTO.format('Bold' if bold else 'Regular')
    if os.path.exists(p):
        for i in range(10):
            try:
                f = ImageFont.truetype(p, size, index=i)
                if f.getname()[0].endswith(' ' + want): return f
            except OSError: break
        return ImageFont.truetype(p, size, index=0)
    return ImageFont.truetype(DEJAVU.format('-Bold' if bold else ''), size)

def wrap(d, text, f, maxw):
    toks = list(text) if CJK.search(text) and ' ' not in text.strip() else re.split(r'(\s+)', text)
    lines, cur = [], ''
    for t in toks:
        if d.textlength(cur + t, font=f) <= maxw or not cur: cur += t
        else: lines.append(cur.strip()); cur = t.lstrip()
    if cur.strip(): lines.append(cur.strip())
    return lines

def motif(d, kind, acc, rng_seed=3):
    dim = tuple(int(c * 0.35) for c in acc)
    if kind == 'chart':
        pts = [(260, 2250), (620, 2050), (900, 2160), (1250, 1820), (1560, 1900), (1900, 1500), (2250, 1620), (2740, 1100)]
        for x in range(260, 2800, 160): d.line([(x, 1100), (x, 2400)], fill=dim, width=4)
        d.line(pts, fill=acc, width=34, joint='curve')
        for p in pts: d.ellipse([p[0] - 34, p[1] - 34, p[0] + 34, p[1] + 34], fill=acc)
    elif kind == 'waves':
        for k in range(7):
            amp = 120 - k * 12; y0 = 1500 + k * 140
            pts = [(x, y0 + amp * math.sin(x / 380 + k * 0.7)) for x in range(0, W + 20, 20)]
            d.line(pts, fill=tuple(int(c * (0.9 - k * 0.1)) for c in acc), width=14)
    elif kind == 'grid':
        for x in range(0, W, 150): d.line([(x, 1500), (x, 2450)], fill=dim, width=3)
        for y in range(1500, 2450, 150): d.line([(0, y), (W, y)], fill=dim, width=3)
        import random; r = random.Random(rng_seed)
        for _ in range(14):
            x, y = r.randrange(1, 19) * 150, r.randrange(12, 17) * 150; x2 = x + r.choice([-1, 1]) * r.randrange(2, 6) * 150
            d.line([(x, y), (x2, y), (x2, y - 300)], fill=acc, width=16); d.ellipse([x2 - 30, y - 330, x2 + 30, y - 270], fill=acc)
    elif kind == 'sun':
        for k in range(6): r = 900 - k * 140; d.arc([1500 - r, 2300 - r, 1500 + r, 2300 + r], 180, 360, fill=acc, width=22)
        d.rectangle([0, 2300, W, 2330], fill=acc)
    else:   # bars: a sound-wave
        for i, x in enumerate(range(300, 2750, 110)):
            h = 120 + 520 * abs(math.sin(i * 0.55)) * (0.6 + 0.4 * math.sin(i * 0.21))
            d.rounded_rectangle([x, 1800 - h, x + 60, 1800 + h], 30, fill=acc)

def make(show, out):
    st = show['music'].get('style', 'news'); pal = dict(PALETTES.get(st, PALETTES['none']))
    pal.update({k: tuple(v) for k, v in (show['cover'].get('palette') or {}).items()})
    kind = show['cover'].get('motif') or pal['motif']
    img = Image.new('RGB', (W, W)); d = ImageDraw.Draw(img)
    a, b = pal['bg_top'], pal['bg_bottom']
    for y in range(W):
        k = y / W; d.line([(0, y), (W, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    motif(d, kind, pal['accent'])
    lang = show['language']; title = show['title']
    d.rectangle([260, 300, 520, 330], fill=pal['accent'])
    for size in range(420, 150, -20):     # biggest size whose wrapped title fits in 3 lines above the motif (y<1400)
        f = font(lang, size); lines = wrap(d, title, f, W - 520)
        if len(lines) <= 3 and all(d.textlength(l, font=f) <= W - 520 for l in lines) and 380 + len(lines) * size * 1.18 < 1420: break
    if len(lines) > 1:                    # balance the lines (avoid a long first line and an orphan)
        for k in (1.05, 1.15, 1.25, 1.35):
            bal = wrap(d, title, f, d.textlength(title, font=f) / len(lines) * k)
            if len(bal) == len(lines): lines = bal; break
    y = 380
    for l in lines: d.text((260, y), l, font=f, fill=pal['text']); y += int(size * 1.18)
    tag = show['cover'].get('tagline') or show.get('subtitle') or ''
    if tag:
        f2 = font(lang, 120, bold=False)
        for i, l in enumerate(wrap(d, tag, f2, W - 520)[:2]): d.text((270, 2560 + i * 150), l, font=f2, fill=pal['accent'])
    img.save(out, 'JPEG', quality=88, optimize=True)
    print(out, os.path.getsize(out), 'bytes')

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('show'); ap.add_argument('--out'); a = ap.parse_args()
    s = pk.load_show(a.show); out = a.out or pk.show_paths(s)['cover']; os.makedirs(os.path.dirname(out), exist_ok=True)
    make(s, out)
