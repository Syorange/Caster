#!/usr/bin/env python3
"""Render a cue script for a show into a finished, loudness-normalised MP3 (ID3 tags + cover).

usage: render.py SHOW_DIR SCRIPT OUT.mp3 --title T --date YYYY-MM-DD [--desc D] [--type T]
       render.py SHOW_DIR SCRIPT --check        lint only (no TTS): syntax, speakers, length vs target, cue limits

Script syntax (UTF-8, one cue per line; blank lines and lines starting with # are ignored):
  A: text                      line by host with id "A" (voice/prosody from show.json hosts)
  A(-3%): text                 per-line rate override        A(+2%,+1Hz): text   rate + pitch
  A(+0%,+0Hz,-10%): text       rate, pitch, volume           (keep overrides within about ±8%, ±3Hz)
  @speaker Name | f | guest [| xx-XX-SomeNeural]   declare an extra voice (clip, quote reader, translated speaker);
                               without an explicit voice, the next free voice of that gender for the show language
  Name: text                   line by a declared speaker
  [INTRO] [OUTRO] [STING] [SWOOSH]   music / SFX cues (speech starts at each asset's cue point, music ducks under it)
  [BED ON] ... [BED OFF]       background bed loop under the lines in between (ducked to bed_under_voice_lu)
  [PAUSE 0.8]                  silence in seconds
Stray prosody tags anywhere in the text (e.g. "text (+2%,+1Hz)") are applied and removed, never spoken."""
import argparse, asyncio, hashlib, json, os, re, subprocess, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pk, voices as V
SR = pk.SR

LINE = re.compile(r'^([^:：\[\]()#@]{1,40}?)(?:\(([^)]*)\))?\s*[:：]\s*(.+)$')
_TOK = r'\s*[+\-－]\s*\d+(?:\.\d+)?\s*(?:%|％|[Hh][Zz])\s*'
TAG = re.compile(r'[(（]' + _TOK + r'(?:[,，、]' + _TOK + r'){0,2}[)）]')
SPK = re.compile(r'^@speaker\s+(.+?)\s*\|\s*([mf])\s*(?:\|\s*(\w+))?\s*(?:\|\s*([\w-]+Neural))?\s*$')
CUE = re.compile(r'^\[(INTRO|STING|SWOOSH|OUTRO|BED ON|BED OFF|PAUSE)\s*([\d.]*)\]$', re.I)

def extract_prosody(text):
    """-> ('rate,pitch,vol' or None, text without tag groups)"""
    found = [m.group(0) for m in TAG.finditer(text)]
    if not found: return None, text
    toks = re.findall(r'[+\-－]\s*\d+(?:\.\d+)?\s*(?:%|％|[Hh][Zz])', found[-1])
    toks = [re.sub(r'\s', '', t).replace('－', '-').replace('％', '%') for t in toks]
    toks = [t[:-2] + 'Hz' if t.lower().endswith('hz') else t for t in toks]
    pct = [t for t in toks if t.endswith('%')]
    rate = pct[0] if pct else ''; vol = pct[1] if len(pct) > 1 else ''
    pitch = next((t for t in toks if t.endswith('Hz')), '')
    return f'{rate},{pitch},{vol}', TAG.sub('', text).strip()

def scrub(text):
    """Last safety pass before TTS: drop leftover prosody-looking fragments (signed Hz values, bracketed signed %);
    ordinary numbers like 100% or 'up 2%' stay."""
    t = re.sub(r'[(（]?\s*[+\-－]\s*\d+(?:\.\d+)?\s*[%％]?\s*[,，、]?\s*[+\-－]?\s*\d+(?:\.\d+)?\s*[Hh][Zz]\s*(?:[,，、]\s*[+\-－]\s*\d+(?:\.\d+)?\s*[%％])?\s*[)）]?', '', text)
    t = re.sub(r'[(（]?\s*[+\-－]\s*\d+(?:\.\d+)?\s*[Hh][Zz]\s*[)）]?', '', t)
    t = re.sub(r'[(（]\s*[+\-－]\s*\d+(?:\.\d+)?\s*[%％]\s*[)）]', '', t)
    t = re.sub(r'[(（]\s*[)）]', '', t)
    return t.strip()

def check_no_tags(says):
    bad = [p['text'] for p in says if re.search(r'\d\s*[Hh][Zz]\b|[(（]\s*[+\-－]\d', p['text'])]
    if bad: pk.die('prosody tag would be spoken: ' + bad[0][:60])

def parse(show, path):
    hosts = {h['id']: h for h in show['hosts']}
    voices = {k: h['voice'] for k, h in hosts.items()}; used = set(voices.values()); pool = None
    cues, assigned, errs = [], {}, []
    for n, raw in enumerate(open(path, encoding='utf-8'), 1):
        s = raw.strip()
        if not s or s.startswith('#'): continue
        m = SPK.match(s)
        if m:
            name, g, role, explicit = m.groups()
            if name in hosts and not explicit: continue        # a show.json host keeps its configured voice
            if explicit and not V.exists(explicit): errs.append(f'line {n}: unknown voice {explicit}')
            v = explicit
            if not v:
                pool = pool or V.pool(show)
                free = [x for x in pool[g] if x not in used]
                v = free[0] if free else pool[g][len(assigned) % len(pool[g])]
            voices[name] = v; used.add(v); assigned[name] = v; continue
        if s.startswith('@'): errs.append(f'line {n}: unknown directive {s[:30]}'); continue
        m = CUE.match(s)
        if m: cues.append(('cue', m.group(1).upper(), float(m.group(2) or 0), n)); continue
        if s.startswith('['): errs.append(f'line {n}: unknown cue {s[:30]} (INTRO OUTRO STING SWOOSH BED ON/OFF PAUSE x)'); continue
        m = LINE.match(s)
        if not m: errs.append(f'line {n}: cannot parse (expected "SPEAKER: text"): {s[:50]}'); continue
        h, opts, text = m.groups(); h = h.strip()
        if TAG.search(h):
            name_opts, h = extract_prosody(h); opts = opts or name_opts
        stray, text = extract_prosody(text)
        if stray and not opts: opts = stray
        text = scrub(text)
        if not text: errs.append(f'line {n}: empty text after removing prosody tags'); continue
        if h not in voices: errs.append(f'line {n}: undeclared speaker {h!r} (hosts: {", ".join(hosts)}; add an @speaker line)'); continue
        base = hosts.get(h) or (show.get('voice_defaults') or {}).get(voices[h], {})
        rate, pitch, vol = base.get('rate', '+0%'), base.get('pitch', '+0Hz'), base.get('volume', '+0%')
        if opts:
            o = [x.strip() for x in opts.split(',')]
            rate = o[0] or rate
            if len(o) > 1 and o[1]: pitch = o[1]
            if len(o) > 2 and o[2]: vol = o[2]
        for val, pat in ((rate, r'[+-]\d+%'), (pitch, r'[+-]\d+Hz'), (vol, r'[+-]\d+%')):
            if not re.fullmatch(pat, val): errs.append(f'line {n}: bad prosody value {val!r} (use e.g. +4%, -2Hz)')
        cues.append(('say', h, dict(voice=voices[h], rate=rate, pitch=pitch, volume=vol, text=text), n))
    return cues, assigned, errs

def lint(show, cues, errs):
    L, F = show['length'], show['format']; warn = []
    says = [c[2]['text'] for c in cues if c[0] == 'say']
    units = sum(pk.count_len(t, L['unit']) for t in says)
    est = units / L['per_minute']; tgt = L['target_minutes']
    long_ = [t for t in says if (len(t) if L['unit'] == 'chars' else len(t)) > F['max_line_chars']]
    names = [c[1] for c in cues if c[0] == 'cue']
    n_sting = names.count('STING') + names.count('SWOOSH'); n_bed = names.count('BED ON')
    if names.count('BED ON') != names.count('BED OFF'): warn.append('BED ON/BED OFF are not paired')
    if n_sting > F['max_stings']: warn.append(f'{n_sting} stings/swooshes > max_stings {F["max_stings"]}')
    if n_bed > F['max_bed_sections']: warn.append(f'{n_bed} bed sections > max_bed_sections {F["max_bed_sections"]}')
    if long_: warn.append(f'{len(long_)} lines longer than {F["max_line_chars"]} chars, e.g. {long_[0][:40]}...')
    if abs(est - tgt) > tgt * L.get('tolerance', 0.1): warn.append(f'estimated {est:.1f} min vs target {tgt} min')
    if 'INTRO' not in names: warn.append('no [INTRO]')
    if 'OUTRO' not in names: warn.append('no [OUTRO]')
    return dict(lines=len(says), units=units, unit=L['unit'], est_minutes=round(est, 1), target_minutes=tgt,
                stings=n_sting, bed_sections=n_bed, errors=errs, warnings=warn)

async def tts_one(p, sem, cache):
    import edge_tts
    key = hashlib.sha1(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    out = os.path.join(cache, key + '.mp3')
    if os.path.exists(out) and os.path.getsize(out) > 0: return out
    async with sem:
        for attempt in range(5):
            try:
                c = edge_tts.Communicate(p['text'], p['voice'], rate=p['rate'], pitch=p['pitch'], volume=p['volume'])
                await c.save(out + '.tmp'); os.replace(out + '.tmp', out); return out
            except Exception as e:
                print('tts retry', attempt, e, file=sys.stderr); await asyncio.sleep(2 + attempt * 3)
    raise RuntimeError('TTS failed: ' + p['text'][:30])

async def tts_all(ps, cache, conc=6):
    sem = asyncio.Semaphore(conc)
    return await asyncio.gather(*(tts_one(p, sem, cache) for p in ps))

def trim(x, thr=0.006, pad=0.03):
    idx = np.where(np.abs(x) > thr)[0]
    if not len(idx): return x
    p = int(pad * SR); x = x[max(0, idx[0] - p): idx[-1] + p].copy()
    k = min(int(0.008 * SR), len(x) // 2); w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, k))
    x[:k] *= w; x[-k:] *= w[::-1]                     # raised-cosine edges: no clicks at clip boundaries
    return x

def envelope(active, hold_s, attack_s, release_s, look_s, fr=100):
    """Side-chain gain envelope at frame rate fr: activity -> look-ahead -> hold -> asymmetric one-pole smoothing."""
    a = active.astype(float); n = len(a); c = np.concatenate([[0.0], np.cumsum(a)]); i = np.arange(n)
    L, H = int(look_s * fr), int(hold_s * fr)
    ahead = c[np.minimum(i + 1 + L, n)] - c[i]               # any speech within the next L frames (duck early)
    behind = c[i + 1] - c[np.maximum(i + 1 - H, 0)]          # or within the last H frames (hold through short gaps)
    a = ((ahead > 0) | (behind > 0)).astype(float)
    ka, kr = 1 - np.exp(-1 / (attack_s * fr)), 1 - np.exp(-1 / (release_s * fr))
    out = np.empty(n); y = 0.0
    for k, v in enumerate(a):
        y += (ka if v > y else kr) * (v - y); out[k] = y
    return out

def build(show, cues, files):
    P = pk.show_paths(show); A = show['audio']
    meta = json.load(open(os.path.join(P['assets'], 'music_meta.json'))) if os.path.exists(os.path.join(P['assets'], 'music_meta.json')) else {}
    music = {k.upper(): (pk.decode(os.path.join(P['assets'], v['file']), 2), v['cue_point'])
             for k, v in meta.items() if isinstance(v, dict) and k != 'bed' and os.path.exists(os.path.join(P['assets'], v['file']))}
    bed_loop = pk.decode(os.path.join(P['assets'], 'bed.wav'), 2) if 'bed' in meta else None
    says = [c for c in cues if c[0] == 'say']; hosts = [c[1] for c in says]
    clips = [trim(pk.decode(f, 1)) for f in files]
    def arms(x):
        a = x[np.abs(x) > 0.01]; return np.sqrt(np.mean(a ** 2)) if len(a) else 1e-3
    for h in set(hosts):          # level-match speakers (edge-tts voices differ by up to ~7 dB), then each line gently
        g = 0.12 / arms(np.concatenate([c for c, hh in zip(clips, hosts) if hh == h]))
        for i, hh in enumerate(hosts):
            if hh == h: clips[i] = clips[i] * g
    clips = [c * (0.12 / arms(c)) ** 0.5 for c in clips]
    ev_v, ev_m, beds, missing = [], [], [], set()
    t, prev, bed0, ci = 0.4, None, None, 0
    for c in cues:
        kind, a, b = c[0], c[1], c[2]
        if kind == 'say':
            x = clips[ci]; ci += 1
            if prev is not None: t += A['turn_gap_s'] if a != prev else A['same_speaker_gap_s']
            ev_v.append((t, x)); t += len(x) / SR; prev = a
        elif a == 'PAUSE': t += b
        elif a in ('INTRO', 'OUTRO', 'STING', 'SWOOSH'):
            if a not in music: missing.add(a); t += 0.6; prev = None; continue
            x, cue = music[a]; t += 0.3 if a in ('INTRO', 'SWOOSH') else 0.4
            lu = {'STING': A['sting_lu'], 'SWOOSH': A['swoosh_lu']}.get(a, A['music_lu'])
            ev_m.append((t, x, 10 ** (lu / 20)))
            t += (len(x) / SR) if a == 'OUTRO' else cue; prev = None
        elif a == 'BED ON': bed0 = t
        elif a == 'BED OFF' and bed0 is not None: beds.append((bed0, t + 1.2)); bed0 = None
    if bed0 is not None: beds.append((bed0, t + 1.2))
    total = int((t + 1.0) * SR)
    voice = np.zeros(total)
    for st, x in ev_v:
        i = int(st * SR); voice[i:i + len(x)] += x[:total - i]
    vstereo = np.stack([voice, voice], 1)
    vg = 10 ** ((-18.0 - pk.lufs(vstereo)) / 20); voice *= vg; vstereo *= vg      # voice reference: -18 LUFS
    mus = np.zeros((total, 2)); bed = np.zeros((total, 2)); bedmask = np.zeros(total, bool)
    for st, x, g in ev_m:
        i = int(st * SR); n = min(len(x), total - i); mus[i:i + n] += x[:n] * g
    if bed_loop is not None:
        for s0, s1 in beds:
            i0, i1 = int(s0 * SR), min(total, int(s1 * SR)); n = i1 - i0
            reps = np.tile(bed_loop, (n // len(bed_loop) + 1, 1))[:n]
            f = min(int(2.5 * SR), n // 2); w = np.ones(n)
            w[:f] = np.sin(np.linspace(0, np.pi / 2, f)) ** 2; w[n - f:] *= np.cos(np.linspace(0, np.pi / 2, f)) ** 2
            bed[i0:i1] += reps * w[:, None]; bedmask[i0:i1] = True
    elif beds: missing.add('BED')
    # side-chain: 10 ms frame RMS of the voice
    fr = 100; hop = SR // fr; nf = total // hop
    rms = np.sqrt(np.mean(voice[:nf * hop].reshape(nf, hop) ** 2, axis=1) + 1e-12)
    act = rms > np.percentile(rms[rms > 1e-5], 90) * 10 ** (-30 / 20) if np.any(rms > 1e-5) else rms > 1
    env_b = envelope(act, 0.6, 0.08, 0.6, 0.15)
    env_m = envelope(act, 0.15, 0.05, 0.35, 0.12)
    up = lambda e: np.interp(np.arange(total), np.arange(nf) * hop + hop / 2, e)
    eb, em = up(env_b), up(env_m)
    bed_db = A['bed_in_gaps_lu'] + (A['bed_under_voice_lu'] - A['bed_in_gaps_lu']) * eb
    bed_d = bed * (10 ** (bed_db / 20))[:, None]
    mus_d = mus * (10 ** (-A['music_duck_db'] * em / 20))[:, None]
    mix = vstereo + mus_d + bed_d
    # measured stem relations (what a listener hears): bed under speech vs voice, music cues vs voice
    vmask = np.zeros(total, bool); vmask[:nf * hop] = np.repeat(act, hop)
    speech_s = sum(len(x) for _, x in ev_v) / SR + sum(A['turn_gap_s'] for _ in ev_v[1:])
    stats = dict(voice_lufs_ref=-18.0, speech_seconds=round(speech_s, 1))
    if bedmask.any():
        m = bedmask & vmask
        stats['bed_under_voice_rel_lu'] = round(pk.lufs(bed_d, mask=m) - pk.lufs(vstereo, mask=m), 1)
        if (bedmask & ~vmask).sum() > SR: stats['bed_in_gaps_rel_lu'] = round(pk.lufs(bed_d, mask=bedmask & ~vmask) + 18.0, 1)
    if ev_m: stats['music_cues_unducked_rel_lu'] = round(pk.lufs(mus) + 18.0, 1)
    return mix, total / SR, missing, stats

def ebur128(path):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    tail = r[r.rfind('Summary:'):]
    g = lambda k: float(re.search(k + r':\s+(-?[\d.]+|-inf)', tail).group(1))
    return dict(integrated_lufs=g('I'), lra_lu=g('LRA'), true_peak_dbtp=g('Peak'))

def limiter(x, ceil_db, look_s=0.005, release_s=0.08):
    """Look-ahead true-peak limiter. Peaks are measured on a 4x oversampled signal in 1 ms blocks; the gain curve is a
    min-filter (+-look) -> one-pole release -> box smoothing, which guarantees gain <= required gain at every peak."""
    from scipy.signal import resample_poly
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    c = 10 ** (ceil_db / 20); B = SR // 1000; n = len(x); nb = (n + B - 1) // B
    ab = np.abs(x).max(axis=1); pk_ = np.pad(ab, (0, nb * B - n)).reshape(nb, B).max(axis=1)   # sample peaks per block
    cand = np.where(pk_ > c * 10 ** (-3.5 / 20))[0]        # inter-sample peaks exceed sample peaks by < ~3.5 dB here
    if len(cand):                                           # oversample 4x only around candidate blocks
        cuts = np.where(np.diff(cand) > 20)[0]; segs = np.split(cand, cuts + 1)
        for sg in segs:
            a0, b0 = sg[0] * B, min(n, (sg[-1] + 1) * B); a, b = max(0, a0 - 256), min(n, b0 + 256)
            o = np.abs(resample_poly(x[a:b], 4, 1, axis=0)).max(axis=1)[(a0 - a) * 4:(b0 - a) * 4]
            blk = np.pad(o, (0, (-len(o)) % (4 * B))).reshape(-1, 4 * B).max(axis=1)
            pk_[sg[0]:sg[0] + len(blk)] = np.maximum(pk_[sg[0]:sg[0] + len(blk)], blk[:nb - sg[0]])
    greq = np.minimum(1.0, c / np.maximum(pk_, 1e-9)); L = max(1, int(look_s * 1000))
    g = minimum_filter1d(greq, 2 * L + 1)
    kr = 1 - np.exp(-1 / (release_s * 1000)); y = 1.0
    for i in range(nb):
        y = min(g[i], y + (1 - y) * kr); g[i] = y
    g = uniform_filter1d(g, L + 1)
    gs = np.interp(np.arange(n), np.arange(nb) * B + B / 2, g)
    return x * gs[:, None], float(20 * np.log10(g.min()))

def master_encode(show, mix, out_mp3, meta, cover):
    """Gain to target LUFS, true-peak limit (ceiling 0.5 dB under target for MP3 overshoot), encode, then measure the
    MP3 itself with ffmpeg ebur128 and correct up to 3 times until |I - target| <= 0.5 LU and TP <= target."""
    A = show['audio']; T, TP = float(A['lufs']), float(A['true_peak'])
    if int(A['channels']) == 1: mix = np.repeat(mix.mean(axis=1, keepdims=True), 2, axis=1)
    ceil, adj, attempts = TP - 0.5, 0.0, []
    for k in range(4):
        x = mix.copy()
        for _ in range(2):                                   # limiting lowers loudness a little: iterate twice
            x *= 10 ** ((T + adj - pk.lufs(x)) / 20); x, gr = limiter(x, ceil)
        tmp = out_mp3 + '.f32'; x.astype('<f4').tofile(tmp)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', tmp, '-c:a', 'libmp3lame',
                        '-b:a', A['bitrate'], '-ar', str(SR), '-ac', str(A['channels']), out_mp3], check=True)
        os.remove(tmp); m = ebur128(out_mp3); m['max_gain_reduction_db'] = round(-gr, 1); attempts.append(m)
        if abs(m['integrated_lufs'] - T) <= 0.5 and m['true_peak_dbtp'] <= TP: break
        if m['true_peak_dbtp'] > TP: ceil -= (m['true_peak_dbtp'] - TP) + 0.2
        adj += T - m['integrated_lufs']
    meta['_master'] = attempts
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TCON, COMM, APIC, TLAN
    lang = pk.ISO639_2.get(pk.lang_base(show['language']), 'und')
    tags = ID3(); tags.add(TIT2(encoding=3, text=meta['title'])); tags.add(TPE1(encoding=3, text=show['author']))
    tags.add(TALB(encoding=3, text=meta['album'])); tags.add(TDRC(encoding=3, text=meta['date']))
    tags.add(TCON(encoding=3, text='Podcast')); tags.add(TLAN(encoding=3, text=lang))
    tags.add(COMM(encoding=3, lang=lang, desc='', text=meta.get('desc', '')))
    if cover and os.path.exists(cover):
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=open(cover, 'rb').read()))
    tags.save(out_mp3, v2_version=3)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('show'); ap.add_argument('script'); ap.add_argument('out', nargs='?')
    ap.add_argument('--title'); ap.add_argument('--date'); ap.add_argument('--desc', default=''); ap.add_argument('--type')
    ap.add_argument('--check', action='store_true'); a = ap.parse_args()
    show = pk.load_show(a.show); P = pk.show_paths(show)
    if not os.path.exists(a.script): pk.die('no script ' + a.script, 4)
    cues, assigned, errs = parse(show, a.script); rep = lint(show, cues, errs)
    if a.check or errs:
        print(json.dumps(rep, ensure_ascii=False, indent=1)); sys.exit(1 if errs else 0)
    if not (a.out and a.title and a.date): ap.error('OUT, --title and --date are required to render')
    says = [c[2] for c in cues if c[0] == 'say']; check_no_tags(says)
    if assigned: print('voices:', json.dumps(assigned, ensure_ascii=False), file=sys.stderr)
    os.makedirs(P['cache'], exist_ok=True); os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out + '.tts.txt', 'w', encoding='utf-8') as f:     # exactly what goes to TTS (audit trail)
        for p in says: f.write(f"{p['voice']}\t{p['rate']}\t{p['pitch']}\t{p['volume']}\t{p['text']}\n")
    t0 = time.time(); files = asyncio.run(tts_all(says, P['cache'])); t1 = time.time()
    mix, dur, missing, stats = build(show, cues, files); t2 = time.time()
    if missing: print('WARN: no asset for ' + ', '.join(sorted(missing)) + ' (cue rendered as a short pause)', file=sys.stderr)
    _, conf = pk.ep_type(show, a.type)
    meta = dict(title=a.title, album=conf['album'], date=a.date, desc=a.desc)
    master_encode(show, mix, a.out, meta, P['cover'])
    out = dict(out=a.out, duration_s=round(dur, 1), lines=len(says), units=rep['units'], unit=rep['unit'],
               measured_pace_per_min=round(rep['units'] / (stats['speech_seconds'] / 60), 1), tts_s=round(t1 - t0, 1), mix_s=round(t2 - t1, 1),
               bytes=os.path.getsize(a.out), mix_stats=stats, final=ebur128(a.out), master_passes=len(meta['_master']),
               limiter_max_gain_reduction_db=meta['_master'][-1]['max_gain_reduction_db'], warnings=rep['warnings'])
    json.dump(out, open(a.out + '.report.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))

if __name__ == '__main__':
    main()
