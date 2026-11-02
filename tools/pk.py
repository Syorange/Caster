#!/usr/bin/env python3
"""podcast-kit common helpers: show config loading/validation, paths, titles, loudness measurement.
Every tool takes a SHOW_DIR (folder containing show.json). Nothing here is specific to any one show."""
import json, os, re, sys, math, subprocess
from datetime import datetime, date as _date
from zoneinfo import ZoneInfo
import numpy as np

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 44100

DEFAULTS = {
    "subtitle": "", "explicit": False, "private": True, "timezone": "UTC", "subcategory": None,
    "owner_email": None, "website": None,
    "length": {"target_minutes": 15, "unit": None, "per_minute": None, "tolerance": 0.1},
    "episode": {"default_type": "ep", "title_template": "{headline}", "types": {}, "source_link_label": "Source"},
    "retention_days": 0,
    "schedule": {"cadence": "on demand", "days": [], "publish_by": None, "notes": ""},
    "sources": [],
    "format": {"segments": [], "tone": "", "rules": [], "max_stings": 6, "max_bed_sections": 3,
               "max_line_chars": None},
    "music": {"style": "news", "transpose": 0, "bpm": None, "seed": 1, "files": {}, "license_note": ""},
    "audio": {"channels": 1, "bitrate": "96k", "lufs": -16.0, "true_peak": -1.5, "lra": 11,
              "music_lu": -3.0, "sting_lu": -4.0, "swoosh_lu": -9.0,
              "bed_under_voice_lu": -26.0, "bed_in_gaps_lu": -17.0, "music_duck_db": 14.0,
              "turn_gap_s": 0.32, "same_speaker_gap_s": 0.42},
    "cover": {"tagline": "", "palette": None, "motif": None},
    "voice_pool": None, "voice_defaults": {},
}
REQUIRED = ["title", "author", "description", "language", "category", "hosts"]
# speaking-rate defaults measured/typical for edge-tts at +0% (chars for CJK, words otherwise)
PACE = {"zh": ("chars", 280), "ja": ("chars", 330), "ko": ("chars", 300), "yue": ("chars", 280)}
ISO639_2 = {"zh": "chi", "en": "eng", "ja": "jpn", "ko": "kor", "es": "spa", "fr": "fre", "de": "ger", "it": "ita",
            "pt": "por", "ru": "rus", "ar": "ara", "hi": "hin", "vi": "vie", "th": "tha", "id": "ind", "nl": "dut",
            "sv": "swe", "pl": "pol", "tr": "tur", "yue": "chi"}
EN_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
EN_DAYS = "Mon Tue Wed Thu Fri Sat Sun".split()

def die(msg, code=1):
    print('ERROR: ' + msg, file=sys.stderr); sys.exit(code)

def merge(d, base):
    out = dict(base)
    for k, v in (d or {}).items():
        out[k] = merge(v, base[k]) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

def slugify(s):
    s = re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    return s or 'show'

def lang_base(lang): return lang.split('-')[0].lower()

def load_show(show_dir_or_json):
    p = show_dir_or_json
    if os.path.isdir(p): p = os.path.join(p, 'show.json')
    if not os.path.exists(p): die(f'no show.json at {p}', 4)
    raw = json.load(open(p, encoding='utf-8'))
    miss = [k for k in REQUIRED if not raw.get(k)]
    if miss: die('show.json missing required: ' + ', '.join(miss))
    s = merge(raw, DEFAULTS)
    s['slug'] = raw.get('slug') or slugify(raw['title'])
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}', s['slug']): die('slug must be lowercase a-z0-9-')
    s['dir'] = os.path.dirname(os.path.abspath(p))
    unit, pm = PACE.get(lang_base(s["language"]), ("words", 165))
    s['length']['unit'] = s['length'].get('unit') or unit
    s['length']['per_minute'] = s['length'].get('per_minute') or pm
    if not s['format'].get('max_line_chars'):
        s['format']['max_line_chars'] = 90 if s['length']['unit'] == 'chars' else 220
    ids = set()
    for h in s['hosts']:
        if not h.get('id') or h['id'] in ids: die('each host needs a unique id (the script label, e.g. "A")')
        if not re.fullmatch(r'[^:：\[\]()#@]{1,40}', h['id']): die(f'bad host id {h["id"]!r}')
        ids.add(h['id']); h.setdefault('name', h['id']); h.setdefault('gender', 'f')
        h.setdefault('rate', '+0%'); h.setdefault('pitch', '+0Hz'); h.setdefault('volume', '+0%'); h.setdefault('role', 'host')
        if not h.get('voice'): die(f'host {h["id"]} has no voice (run new_show.py, which auto-picks one)')
    return s

def show_paths(s):
    d = s['dir']
    return dict(assets=os.path.join(d, 'assets'), episodes=os.path.join(d, 'episodes'), cache=os.path.join(d, 'cache'),
                state=os.path.join(d, 'state'), config=os.path.join(d, 'config.env'), cover=os.path.join(d, 'assets', 'cover.jpg'))

def tz(s): return ZoneInfo(s.get('timezone') or 'UTC')

def ep_type(s, t):
    t = t or s['episode']['default_type']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', t): die('episode type must be lowercase a-z0-9-')
    return t, merge(s['episode']['types'].get(t, {}), {"title_template": s['episode']['title_template'], "album": s['title']})

def episode_title(s, etype, date_iso, headline, number=None):
    _, conf = ep_type(s, etype)
    d = _date.fromisoformat(date_iso); lb = lang_base(s['language'])
    long = {'zh': f'{d.month}月{d.day}日', 'ja': f'{d.month}月{d.day}日', 'ko': f'{d.month}월 {d.day}일',
            'yue': f'{d.month}月{d.day}日'}.get(lb, f'{EN_MONTHS[d.month-1]} {d.day}, {d.year}')
    v = dict(show=s['title'], headline=headline, date=date_iso, year=d.year, month=d.month, day=d.day,
             md_cjk=f'{d.month}月{d.day}日', date_long=long, weekday=EN_DAYS[d.weekday()], n=number or '')
    try: return conf['title_template'].format(**v).strip()
    except KeyError as e: die(f'title_template uses unknown field {e}; allowed: ' + ', '.join(v))

def count_len(text, unit):
    if unit == 'chars':   # CJK-style: count letters/ideographs, ignore spaces and punctuation
        return sum(1 for c in text if c.isalnum())
    return len(re.findall(r"[\w'’-]+", text))

# ---------------- audio measurement (ITU-R BS.1770-4 / EBU R128) ----------------
def _kweight_sos(sr):
    from scipy.signal import tf2sos
    G, Q, fc = 3.999843853973347, 0.7071752369554196, 1681.974450955533     # high shelf (pyloudnorm form)
    A = 10 ** (G / 40); w0 = 2 * math.pi * fc / sr; al = math.sin(w0) / (2 * Q); c = math.cos(w0)
    b1 = [A*((A+1)+(A-1)*c+2*math.sqrt(A)*al), -2*A*((A-1)+(A+1)*c), A*((A+1)+(A-1)*c-2*math.sqrt(A)*al)]
    a1 = [(A+1)-(A-1)*c+2*math.sqrt(A)*al, 2*((A-1)-(A+1)*c), (A+1)-(A-1)*c-2*math.sqrt(A)*al]
    Q2, fc2 = 0.5003270373238773, 38.13547087602444                          # high pass
    w0 = 2 * math.pi * fc2 / sr; al = math.sin(w0) / (2 * Q2); c = math.cos(w0)
    b2 = [(1+c)/2, -(1+c), (1+c)/2]; a2 = [1+al, -2*c, 1-al]
    return np.vstack([tf2sos(b1, a1), tf2sos(b2, a2)])

def lufs(x, sr=SR, mask=None):
    """Integrated loudness (gated). x: (n,) mono-as-single-channel or (n,2). mask: optional bool per sample;
    only 400 ms blocks whose centre is inside the mask count (used to measure 'bed under voice')."""
    from scipy.signal import sosfilt
    x = np.atleast_2d(x.T).T if x.ndim == 1 else x
    if x.ndim == 1: x = x[:, None]
    y = sosfilt(_kweight_sos(sr), x, axis=0)
    blk, hop = int(0.4 * sr), int(0.1 * sr)
    if len(y) < blk: return -float('inf')
    c = np.cumsum(np.vstack([np.zeros((1, y.shape[1])), y ** 2]), axis=0)
    starts = np.arange(0, len(y) - blk + 1, hop)
    z = ((c[starts + blk] - c[starts]) / blk).sum(axis=1)
    if mask is not None: z = z[mask[np.minimum(starts + blk // 2, len(mask) - 1)]]
    if not len(z): return -float('inf')
    L = -0.691 + 10 * np.log10(z + 1e-20)
    z = z[L > -70]
    if not len(z): return -float('inf')
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > rel]
    return float(-0.691 + 10 * np.log10(z.mean())) if len(z) else -float('inf')

def true_peak_db(x):
    from scipy.signal import resample_poly
    x = x if x.ndim == 2 else x[:, None]
    return float(20 * np.log10(max(np.max(np.abs(resample_poly(x, 4, 1, axis=0))), 1e-12)))

def db(v): return float(20 * np.log10(max(v, 1e-12)))

def analyse(x, sr=SR, loop=False):
    """Objective checks for a music/SFX asset or a final mix."""
    st = x if x.ndim == 2 else x[:, None]
    mono = st.mean(axis=1)
    n5 = int(0.005 * sr)
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2; f = np.fft.rfftfreq(len(mono), 1 / sr)
    bands = [(20, 60, 'sub'), (60, 250, 'low'), (250, 2000, 'mid'), (2000, 6000, 'presence'), (6000, 20000, 'air')]
    tot = spec[(f >= 20) & (f < 20000)].sum() + 1e-20
    out = dict(seconds=round(len(st) / sr, 2), lufs=round(lufs(st, sr), 1), true_peak_dbtp=round(true_peak_db(st), 1),
               sample_peak_dbfs=round(db(np.max(np.abs(st))), 1),
               clipped_samples=int(np.sum(np.abs(st) >= 0.999)),
               dc_offset_db=round(db(np.max(np.abs(st.mean(axis=0)))), 1),
               start_5ms_dbfs=round(db(np.sqrt(np.mean(st[:n5] ** 2))), 1),
               end_5ms_dbfs=round(db(np.sqrt(np.mean(st[-n5:] ** 2))), 1),
               bands_pct={b: round(float(100 * spec[(f >= lo) & (f < hi)].sum() / tot), 1) for lo, hi, b in bands},
               spectral_centroid_hz=int(np.sum(f * spec) / np.sum(spec)))
    if st.shape[1] == 2:
        L, R = st[:, 0], st[:, 1]
        out['stereo_correlation'] = round(float(np.sum(L * R) / (np.sqrt(np.sum(L * L) * np.sum(R * R)) + 1e-20)), 2)
        out['mono_fold_loss_db'] = round(lufs(np.stack([mono, mono], 1), sr) - out['lufs'], 1)
    if loop:   # seam: jump between last and first sample vs typical sample-to-sample step
        step = np.median(np.abs(np.diff(st, axis=0))) + 1e-9
        out['loop_seam_jump_x_median_step'] = round(float(np.max(np.abs(st[0] - st[-1])) / step), 1)
    return out

def decode(path, ch=2, sr=SR):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', str(ch), '-ar', str(sr), '-'],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, '<f4').astype(np.float64)
    return a.reshape(-1, ch) if ch > 1 else a

def write_wav(path, st, sr=SR):
    import wave
    st = st if st.ndim == 2 else st[:, None]
    with wave.open(path, 'wb') as w:
        w.setnchannels(st.shape[1]); w.setsampwidth(3); w.setframerate(sr)   # 24-bit
        q = (np.clip(st, -1, 1 - 2 ** -23) * (2 ** 23)).astype('<i4')
        w.writeframes(np.ascontiguousarray(q).view('u1').reshape(-1, 4)[:, :3].tobytes())
