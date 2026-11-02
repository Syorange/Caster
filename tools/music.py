#!/usr/bin/env python3
"""Original music + SFX for a show, synthesised from scratch (numpy/scipy, no samples => no third-party rights),
or imported from user-supplied royalty-free files. Writes SHOW_DIR/assets/{intro,outro,sting,swoosh,bed}.wav (24-bit
44.1 kHz stereo, each normalised to -18 LUFS with true peak <= -1 dBTP), assets/music_meta.json (durations + cue
points used by render.py) and assets/music_report.json (objective QA numbers).

usage: music.py SHOW_DIR                 build from show.json "music" (style preset or files)
       music.py --style news --out DIR   build a preset into any folder (no show needed)
       music.py --list                   list style presets
Styles: news, calm, upbeat-tech, warm-story, none  (+ "transpose" semitones, "bpm" override, "seed")."""
import argparse, json, os, sys
import numpy as np
from scipy.signal import butter, sosfilt, lfilter, stft, istft
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pk
SR = pk.SR
ASSET_LUFS, ASSET_TP = -18.0, -1.0
LEAD = 0.03   # seconds of silence before the first transient of every synth asset

# ---------------------------------------------------------------- theory
NOTE = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
QUAL = {'': (0, 4, 7), 'm': (0, 3, 7), '7': (0, 4, 7, 10), 'maj7': (0, 4, 7, 11), 'm7': (0, 3, 7, 10),
        'sus4': (0, 5, 7), 'sus2': (0, 2, 7), 'add9': (0, 4, 7, 14), 'madd9': (0, 3, 7, 14), 'maj9': (0, 4, 7, 11, 14),
        'm9': (0, 3, 7, 10, 14), '6': (0, 4, 7, 9), 'dim': (0, 3, 6), '7sus4': (0, 5, 7, 10)}

def chord(sym, tr=0):
    """'Dm', 'Bbmaj7', 'C/E' -> (root pitch class, set of pitch classes, bass pc)"""
    main, _, slash = sym.partition('/')
    r = NOTE[main[0]] + (1 if main[1:2] == '#' else -1 if main[1:2] == 'b' else 0)
    q = main[1 + (main[1:2] in ('#', 'b')):]
    if q not in QUAL: raise ValueError('unknown chord quality ' + sym)
    r = (r + tr) % 12
    bass = r if not slash else (NOTE[slash[0]] + (1 if slash[1:2] == '#' else -1 if slash[1:2] == 'b' else 0) + tr) % 12
    return r, [(r + i) % 12 for i in QUAL[q]], bass

def voice_lead(progression, lo=53, hi=76, n_voices=4, tr=0):
    """Smooth voicings: for each chord, choose the voicing (one note per chord tone, doubled root if needed, inside
    lo..hi) with the smallest total semitone movement from the previous voicing (common tones held)."""
    import itertools
    out, prev = [], None
    for sym in progression:
        _, pcs, _ = chord(sym, tr)
        pcs = (pcs + [pcs[0]] * n_voices)[:max(n_voices, 3)] if len(pcs) < n_voices else pcs[:n_voices]
        cands = [[m for m in range(lo, hi + 1) if m % 12 == pc] for pc in pcs]
        best, bc = None, 1e9
        for combo in itertools.product(*cands):
            v = sorted(combo)
            if len(set(v)) < len(v) or v[-1] - v[0] > 19: continue
            cost = (sum(abs(a - b) for a, b in zip(v, prev)) if prev else abs(np.mean(v) - (lo + hi) / 2) * 2) \
                   + sum(8 if b - a == 1 else 1.0 if b - a == 2 else 0 for a, b in zip(v, v[1:]))   # no semitone clusters            # avoid seconds clusters
            if cost < bc: best, bc = v, cost
        out.append(best); prev = best
    return out

def hz(m): return 440.0 * 2 ** ((m - 69) / 12)
def tt(d): return np.arange(int(d * SR)) / SR

# ---------------------------------------------------------------- dsp
def adsr(n, a=0.005, d=0.1, s=0.7, r=0.2, hold=None):
    a, d, r = max(1, int(a * SR)), max(1, int(d * SR)), max(1, int(r * SR))
    sus = max(n - a - d - r, 0) if hold is None else int(hold * SR)
    # exponential-ish curves (sound more natural than linear): attack 1-e^-x, decay/release e^-x
    att = 1 - np.exp(-5 * np.linspace(0, 1, a)); att /= att[-1]
    dec = s + (1 - s) * np.exp(-5 * np.linspace(0, 1, d))
    rel = s * np.exp(-6 * np.linspace(0, 1, r)); rel -= rel[-1]
    env = np.concatenate([att, dec, np.full(sus, s), rel])
    return np.pad(env, (0, max(0, n - len(env))))[:n]

def bq(kind, f, order=2):
    return butter(order, f / (SR / 2), btype=kind, output='sos')

def filt(x, kind, f, order=2): return sosfilt(bq(kind, f, order), x, axis=0)

def peak_eq(x, f0, gain_db, q=0.9):
    A = 10 ** (gain_db / 40); w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q)
    b = [1 + al * A, -2 * np.cos(w), 1 - al * A]; a = [1 + al / A, -2 * np.cos(w), 1 - al / A]
    return lfilter(np.array(b) / a[0], np.array(a) / a[0], x, axis=0)

def polyblep_saw(f, n, ph0=0.0):
    dt = f / SR; t = (ph0 + dt * np.arange(n)) % 1.0
    y = 2 * t - 1
    m = t < dt; x = t[m] / dt; y[m] -= x + x - x * x - 1
    m = t > 1 - dt; x = (t[m] - 1) / dt; y[m] -= x * x + x + x + 1
    return y

class Inst:
    def __init__(s, rng): s.rng = rng

    def keys(s, m, dur, vel=1.0, bright=1.0):          # FM electric piano (tine + body)
        t = tt(dur + 0.6); f = hz(m)
        idx = (1.2 + 1.2 * bright) * np.exp(-t * 5) * vel
        y = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
        y += 0.25 * np.sin(2 * np.pi * 14 * f * t) * np.exp(-t * 40) * vel    # tine 'ping'
        return y * np.exp(-t * 1.6) * adsr(len(t), 0.003, 0.1, 0.85, 0.5, hold=dur) * vel

    def bell(s, m, dur, vel=1.0):                       # FM bell / celesta, inharmonic 3.5:1
        t = tt(dur + 1.5); f = hz(m)
        y = np.sin(2 * np.pi * f * t + 2.2 * np.exp(-t * 2.5) * np.sin(2 * np.pi * 3.5 * f * t))
        y += 0.3 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 4)
        return y * np.exp(-t * 1.8) * adsr(len(t), 0.002, 0.02, 1, 0.6, hold=dur + 0.8) * vel

    def pluck(s, m, dur, vel=1.0, damp=0.996, bright=0.6):   # Karplus-Strong w/ allpass tuning (guitar/harp)
        f = hz(m); P = SR / f; N = int(np.floor(P - 0.5 - 0.1)); d = P - 0.5 - N
        c = (1 - d) / (1 + d); g = damp ** (440 / f) if f > 440 else damp
        n = int((dur + 1.2) * SR); x = np.zeros(n)
        burst = s.rng.standard_normal(N + 2); burst = filt(burst, 'low', 1500 + 7000 * bright, 1)
        x[:N + 2] = burst
        a = np.zeros(N + 3); a[0] = 1; a[1] = c; a[N] -= g / 2 * c; a[N + 1] -= g / 2 * (1 + c); a[N + 2] -= g / 2
        y = lfilter([1, c], a, x)
        y = y / (np.max(np.abs(y)) + 1e-9)
        return y * adsr(n, 0.001, 0.05, 1, 0.25, hold=dur + 0.7) * vel

    def synth_pluck(s, m, dur, vel=1.0, ph=0.0):       # saw through an enveloped low-pass (2 filters crossfaded)
        n = int((dur + 0.3) * SR); f = hz(m)
        y = 0.6 * polyblep_saw(f, n, ph) + 0.4 * polyblep_saw(f * 1.005, n, ph + 0.3)
        env = np.exp(-np.arange(n) / SR * 18)
        y = filt(y, 'low', 5500) * env + filt(y, 'low', 900) * (1 - env)
        return y * adsr(n, 0.002, 0.12, 0.35, 0.15, hold=dur) * vel * 0.6

    def pad(s, notes, dur, vel=1.0, a=0.8, r=1.2, cutoff=1600):
        """Stereo pad: 3 detuned band-limited saws per note, different detune per channel => width."""
        n = int((dur + r) * SR); L = np.zeros(n); R = np.zeros(n)
        for m in notes:
            for det, side in ((-0.09, 'L'), (0.05, 'L'), (0.0, 'C'), (0.09, 'R'), (-0.05, 'R'), (0.14, 'L'), (-0.13, 'R')):
                y = polyblep_saw(hz(m + det), n, s.rng.uniform())
                if side in ('L', 'C'): L += y * (1.0 if side == 'L' else 0.7)
                if side in ('R', 'C'): R += y * (1.0 if side == 'R' else 0.7)
        st = np.stack([L, R], 1) / (len(notes) * 4.2)
        st = filt(filt(st, 'low', cutoff), 'high', 120)
        return st * adsr(n, a, 0.4, 0.85, r, hold=max(dur - a - 0.4, 0))[:, None] * vel

    def bass(s, m, dur, vel=1.0, tone='round'):
        t = tt(dur + 0.15); f = hz(m)
        y = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
        if tone == 'pulse': y = np.tanh(1.8 * y)
        if tone == 'upright':
            y = y + 0.25 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t * 15)
            return filt(y, 'low', 900) * adsr(len(t), 0.004, 0.25, 0.25, 0.12, hold=dur) * vel
        return filt(y, 'low', 700) * adsr(len(t), 0.006, 0.2, 0.75, 0.12, hold=dur) * vel

    def kick(s, vel=1.0):
        t = tt(0.45); f = 48 + 110 * np.exp(-t * 32)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8)
        y += 0.3 * filt(s.rng.standard_normal(len(t)), 'band', np.array([1500, 5000])) * np.exp(-t * 300)  # click
        return y * adsr(len(t), 0.001, 0.01, 1, 0.05) * vel

    def clap(s, vel=1.0):
        t = tt(0.35); n = s.rng.standard_normal(len(t)); env = np.zeros(len(t))
        for k, o in enumerate((0, 0.011, 0.022)):
            i = int(o * SR); env[i:] += np.exp(-(t[:len(t) - i]) * (180 if k < 2 else 22)) * (0.7 if k < 2 else 1)
        return filt(n, 'band', np.array([900, 6000])) * env * vel * 0.6

    def hat(s, vel=1.0, open_=False):
        t = tt(0.25 if open_ else 0.06)
        y = filt(s.rng.standard_normal(len(t)), 'high', 7500, 4) * np.exp(-t * (18 if open_ else 80))
        return y * vel * 0.5

    def shaker(s, vel=1.0):
        t = tt(0.12); env = (1 - np.exp(-t * 60)) * np.exp(-t * 35)
        return filt(s.rng.standard_normal(len(t)), 'band', np.array([5000, 11000]), 2) * env * vel * 0.7

    def tick(s, vel=1.0):          # woodblock / rim
        t = tt(0.07)
        return (np.sin(2 * np.pi * 1750 * t) + 0.4 * np.sin(2 * np.pi * 3100 * t)) * np.exp(-t * 85) * vel * 0.5

    def sweep(s, dur, f0, f1, f2=None, q=0.35, vel=1.0):
        """Band-pass noise sweep in the STFT domain (centre f0 -> f1 [-> f2]); returns stereo with a pan sweep."""
        n = int(dur * SR); x = s.rng.standard_normal(n)
        fr, tm, Z = stft(x, SR, nperseg=1024, noverlap=768)
        k = np.linspace(0, 1, Z.shape[1])
        lc = np.interp(k, [0, 0.7, 1], [np.log(f0), np.log(f1), np.log(f2 or f1)]) if f2 else np.log(f0) + (np.log(f1) - np.log(f0)) * k
        mask = np.exp(-((np.log(fr[:, None] + 1) - lc[None, :]) ** 2) / (2 * q ** 2))
        _, y = istft(Z * mask, SR, nperseg=1024, noverlap=768); y = y[:n]
        if len(y) < n: y = np.pad(y, (0, n - len(y)))
        tn = np.linspace(0, 1, n)
        env = np.sin(np.pi * np.clip(tn / 0.75, 0, 1) ** 1.6 / 2) ** 2 * np.where(tn < 0.75, 1, np.cos((tn - 0.75) / 0.25 * np.pi / 2) ** 2)
        y = y * env / (np.max(np.abs(y)) + 1e-9) * vel
        pan = np.linspace(-0.6, 0.6, n)
        return np.stack([y * np.sqrt((1 - pan) / 2), y * np.sqrt((1 + pan) / 2)], 1)

class Track:
    def __init__(s, dur): s.x = np.zeros((int(dur * SR), 2))
    def add(s, y, at, pan=0.0, g=1.0):
        i = int(round(at * SR))
        if i >= len(s.x): return
        if y.ndim == 1: y = np.stack([y * np.sqrt((1 - pan) / 2), y * np.sqrt((1 + pan) / 2)], 1) * np.sqrt(2)
        y = y[:len(s.x) - i]; s.x[i:i + len(y)] += y * g

def reverb(st, rng, wet=0.2, decay=1.8, predelay=0.02, damp=6000, width=1.0):
    """Stereo convolution reverb: decorrelated exponentially-decaying noise IRs (+ early reflections), damped,
    wet path high-passed at 180 Hz so the low end stays dry and clear."""
    n = int(decay * SR); t = np.arange(n) / SR; out = np.zeros_like(st); irs = []
    base = rng.standard_normal(n)
    for c in range(2):
        own = rng.standard_normal(n); noise = base * (1 - width) + own * width
        ir = noise * np.exp(-t * 6.9 / decay) * (1 - np.exp(-t * 200))
        for d, g in ((0.011, .5), (0.017, .4), (0.023 + 0.004 * c, .35), (0.031, .3)):
            ir[int(d * SR)] += g * (1 if c else -1) ** int(d * 1000)
        ir = filt(ir, 'low', damp); ir = np.concatenate([np.zeros(int(predelay * SR)), ir]); ir /= np.sqrt(np.sum(ir ** 2))
        irs.append(ir)
    N = len(st) + len(irs[0]); F = 1 << (N - 1).bit_length()
    for c in range(2):
        w = np.fft.irfft(np.fft.rfft(st[:, c], F) * np.fft.rfft(irs[c], F), F)[:len(st)]
        out[:, c] = st[:, c] + wet * filt(w, 'high', 180)
    return out

def tidy(st, fade_in=0.004, fade_out=0.06):
    """DC/subsonic removal, raised-cosine edge fades (no clicks), normalise to ASSET_LUFS, cap true peak."""
    st = filt(st, 'high', 25, 2)
    st -= st.mean(axis=0)
    for n, rev in ((int(fade_in * SR), False), (int(fade_out * SR), True)):
        if n:
            w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
            if rev: st[-n:] *= w[::-1, None]
            else: st[:n] *= w[:, None]
    return normalise(st)

def soft_limit(st, ceiling_db=-1.2):
    c = 10 ** (ceiling_db / 20); k = 0.8 * c
    a = np.abs(st); over = a > k
    st = st.copy(); st[over] = np.sign(st[over]) * (k + (c - k) * np.tanh((a[over] - k) / (c - k)))
    return st

def normalise(st):
    g = 10 ** ((ASSET_LUFS - pk.lufs(st)) / 20); st = st * g
    for _ in range(4):
        tp = pk.true_peak_db(st)
        if tp <= ASSET_TP: break
        st = soft_limit(st, ASSET_TP - 0.4)
    return st

def tail_end(track, thresh_db=-54):
    """Trim trailing near-silence but keep the natural decay down to thresh_db."""
    e = np.abs(track).max(axis=1); thr = np.max(e) * 10 ** (thresh_db / 20)
    idx = np.where(e > thr)[0]
    return track[:idx[-1] + int(0.05 * SR)] if len(idx) else track

# ---------------------------------------------------------------- presets
# Every preset: key centre (tonic chord), BPM, progression for intro/outro/bed (chord, beats), a melodic motif
# (beat offset, midi, beats), instrument choices and mix parameters. Intro and outro end with a cadence onto the
# tonic (V-i / IV-V-I / bVI-bVII-i), so they audibly resolve. Melodies are written over the given chords.
PRESETS = {
 'news': dict(desc='Confident newsroom: D minor, 112 BPM, FM keys + pulse bass + ticking clock percussion',
    bpm=112, tonic='Dm', lead='keys', arp='keys', bass_tone='pulse', drums='news', reverb=(0.16, 1.5), pad_cut=1500,
    intro=[('N', 2), ('Dm', 2), ('Bb', 2), ('Gm', 2), ('A7sus4', 1), ('A', 1), ('Dm', 4)],
    melody_intro=[(2, 74, .5), (2.5, 77, .5), (3, 81, 1), (4, 82, .5), (4.5, 81, .5), (5, 77, 1), (6, 79, .5), (6.5, 82, .5),
                  (7, 81, 1), (8, 79, .5), (8.5, 76, .5), (9, 73, 1), (10, 74, 4)],
    outro=[('Bb', 2), ('C', 2), ('Gm', 2), ('A', 2), ('Dm', 5)],
    melody_outro=[(0, 77, 1), (1, 74, 1), (2, 76, 1), (3, 79, 1), (4, 82, 1), (5, 81, 1), (6, 76, 1), (7, 73, 1), (8, 74, 5)],
    sting=[(0, 74), (.25, 77), (.5, 81), (.75, 86)], sting_chord='Dm',
    bed=[('Dm', 4), ('Bb', 4), ('F', 4), ('C', 4), ('Dm', 4), ('Bb', 4), ('Gm', 4), ('A', 4)], bed_pattern='arp8',
    swoosh=(500, 5000, 1200, 0.35, 1.1)),
 'calm': dict(desc='Calm / ambient: F major (Lydian colour), 70 BPM, soft pad + celesta bells + sub bass, no drums',
    bpm=70, tonic='Fmaj7', lead='bell', arp='bell', bass_tone='round', drums=None, reverb=(0.32, 3.0), pad_cut=1100,
    intro=[('Fmaj7', 2), ('Dm7', 2), ('Bbmaj7', 2), ('C7sus4', 1), ('C', 1), ('Fmaj9', 4)],
    melody_intro=[(0, 72, 1), (1, 76, 1), (2, 77, 1.5), (3.5, 74, .5), (4, 74, 1), (5, 81, 1), (6, 79, 1), (7, 76, 1), (8, 77, 4)],
    outro=[('Bbmaj7', 2), ('Gm7', 2), ('C7sus4', 1), ('C', 1), ('Fmaj7', 5)],
    melody_outro=[(0, 81, 1), (1, 77, 1), (2, 79, 1), (3, 74, 1), (4, 76, 1), (5, 79, 1), (6, 77, 5)],
    sting=[(0, 72), (.33, 77), (.66, 81)], sting_chord='Fmaj7',
    bed=[('Fmaj7', 8), ('Am7', 8), ('Bbmaj7', 8), ('Csus2', 8)], bed_pattern='sparse',
    swoosh=(300, 2500, 700, 0.45, 1.6)),
 'upbeat-tech': dict(desc='Upbeat tech: A minor, 122 BPM, synth plucks in 16ths, side-chain pumped pad, four-on-the-floor kick + clap',
    bpm=122, tonic='Am', lead='synth_pluck', arp='synth_pluck', bass_tone='round', pump=True, drums='dance', reverb=(0.14, 1.2), pad_cut=2200,
    intro=[('N', 4), ('Am', 2), ('F', 2), ('C', 2), ('G', 1), ('E', 1), ('Am', 4)],
    melody_intro=[(4, 76, .5), (4.5, 79, .5), (5, 81, 1), (6, 77, .5), (6.5, 76, .5), (7, 77, 1), (8, 79, .5), (8.5, 76, .5),
                  (9, 72, 1), (10, 74, .5), (10.5, 71, .5), (11, 68, 1), (12, 69, 4)],
    outro=[('F', 2), ('G', 2), ('Esus4', 1), ('E', 1), ('Am', 5)],
    melody_outro=[(0, 81, 1), (1, 77, 1), (2, 79, 1), (3, 83, 1), (4, 81, .5), (4.5, 80, 1.5), (6, 81, 5)],
    sting=[(0, 69), (.125, 72), (.25, 76), (.375, 81)], sting_chord='Am',
    bed=[('Am', 4), ('F', 4), ('C', 4), ('G', 4)] * 2, bed_pattern='arp16',
    swoosh=(800, 8000, 2000, 0.3, 0.9)),
 'warm-story': dict(desc='Warm storytelling: G major, 84 BPM, fingerpicked plucked guitar + soft keys + upright bass + shaker',
    bpm=84, tonic='G', lead='pluck', arp='pluck', bass_tone='upright', drums='brush', reverb=(0.22, 2.0), pad_cut=1300,
    intro=[('N', 1), ('G', 2), ('Em', 2), ('C', 2), ('D', 2), ('G', 4)],
    melody_intro=[(1, 71, 1), (2, 74, 1), (3, 76, 1), (4, 71, 1), (5, 72, 1), (6, 76, .5), (6.5, 74, .5), (7, 72, 1),
                  (8, 74, 1), (9, 66, 1), (10, 67, 4)],
    outro=[('C', 2), ('D', 2), ('Em', 1), ('D/F#', 1), ('G', 5)],
    melody_outro=[(0, 76, 1), (1, 72, 1), (2, 74, 1), (3, 78, 1), (4, 79, 1), (5, 78, 1), (6, 79, 5)],
    sting=[(0, 67), (.25, 71), (.5, 74), (.75, 79)], sting_chord='G',
    bed=[('G', 4), ('Em', 4), ('C', 4), ('D', 4), ('G', 4), ('Em', 4), ('Am7', 4), ('D', 4)], bed_pattern='fingerpick',
    swoosh=(400, 3500, 900, 0.4, 1.2)),
}

class Composer:
    def __init__(s, p, tr=0, bpm=None, seed=1):
        s.p, s.tr = p, tr; s.B = 60 / (bpm or p['bpm']); s.rng = np.random.default_rng(seed); s.I = Inst(s.rng)

    def play(s, inst, m, dur, vel):
        f = getattr(s.I, inst); return f(m + s.tr, dur, vel)

    def harmony(s, tk, prog, t0=0.0, pad_vel=0.34, bass_vel=0.1, comp=True, last_ring=1.6):
        syms = [c for c, _ in prog if c != 'N']; voic = iter(voice_lead(syms, tr=s.tr)); B = s.B; at = t0
        for i, (sym, beats) in enumerate(prog):
            if sym == 'N': at += beats * B; continue
            v = next(voic); _, _, bass = chord(sym, s.tr); d = beats * B; last = i == len(prog) - 1
            pd = s.I.pad(v, d + (last_ring if last else 0.15), 1.0, a=0.05 if comp else 0.5, r=1.4 if last else 0.5,
                         cutoff=s.p['pad_cut'])
            if s.p.get('pump') and not last:   # side-chain 'pump': pad dips on every beat like it is keyed by the kick
                ph = (np.arange(len(pd)) / SR) % B; pd = pd * (1 - 0.55 * np.exp(-ph / 0.11))[:, None]
            tk.add(pd, at, g=pad_vel)
            bm = 36 + (bass - 36) % 12
            tk.add(s.I.bass(bm, d * (1.6 if last else 0.92), 1.0, s.p['bass_tone']), at, g=bass_vel)
            if comp and s.p['arp'] != 'bell':   # chord stab in the comp instrument on the downbeat
                for k, m in enumerate(v):
                    tk.add(getattr(s.I, s.p['arp'])(m, min(d, 1.5) * (2 if last else 1), 0.5), at + k * 0.012,
                           pan=-0.5 + k / max(1, len(v) - 1), g=0.32)
            at += d
        return at

    def melody(s, tk, mel, t0=0.0, g=0.5):
        for (b, m, d) in mel:
            tk.add(s.play(s.p['lead'], m, d * s.B, 0.9), t0 + b * s.B, pan=0.12, g=g)
            if s.p['lead'] in ('keys', 'bell'):          # quiet octave double, opposite side, for width
                tk.add(s.play(s.p['lead'], m - 12, d * s.B, 0.5), t0 + b * s.B + 0.008, pan=-0.3, g=g * 0.35)

    def drums(s, tk, beats, t0=0.0, density=1.0, final_hit=None):
        I, B, kind = s.I, s.B, s.p['drums']
        if not kind: return
        for i in range(int(beats)):
            at = t0 + i * B
            if kind == 'news':
                if i % 2 == 0: tk.add(I.kick(0.9 if i % 4 == 0 else 0.6), at, g=0.5 * density)
                tk.add(I.tick(0.8 if i % 2 else 0.5), at + B / 2, pan=0.35, g=0.22 * density)
                tk.add(I.hat(0.5), at + B / 2, pan=-0.35, g=0.25 * density)
            elif kind == 'dance':
                tk.add(I.kick(1.0), at, g=0.22 * density)
                if i % 2 == 1: tk.add(I.clap(0.9), at, pan=0.05, g=0.3 * density)
                tk.add(I.hat(0.6), at + B / 2, pan=0.3, g=0.28 * density)
                for k in (1, 3): tk.add(I.hat(0.25), at + k * B / 4, pan=-0.3, g=0.2 * density)
            elif kind == 'brush':
                for k in range(2): tk.add(I.shaker(0.9 if k == 0 else 0.6), at + k * B / 2, pan=0.4, g=0.22 * density)
                if i % 4 == 0: tk.add(I.kick(0.5), at, g=0.25 * density)
        if final_hit is not None:
            tk.add(I.kick(1.0), final_hit, g=0.55); tk.add(I.hat(0.6, True), final_hit, pan=0.3, g=0.2)

    def bed_pattern(s, tk, prog, t0=0.0):
        B, pat, at = s.B, s.p['bed_pattern'], t0
        voic = voice_lead([c for c, _ in prog], lo=60, hi=79, n_voices=3, tr=s.tr)
        for (sym, beats), v in zip(prog, voic):
            n8 = int(beats * 2)
            if pat in ('arp8', 'arp16'):
                step = 0.5 if pat == 'arp8' else 0.25; seq = [v[0], v[1], v[2], v[1] + 12 if v[1] + 12 < 88 else v[1]]
                for k in range(int(beats / step)):
                    tk.add(s.play(s.p['arp'], seq[k % 4], step * B * 0.9, 0.55 if k % 2 else 0.75), at + k * step * B,
                           pan=(-0.45 if k % 2 else 0.45), g=0.26)
            elif pat == 'fingerpick':
                _, _, bass = chord(sym, s.tr); low = 43 + (bass - 43) % 12
                seq = [low, v[1], v[2], v[0] + 12, low + 7 if low + 7 < v[0] else v[0], v[2], v[1], v[2]]
                for k in range(n8): tk.add(s.I.pluck(seq[k % 8], B * 0.6, 0.7 if k % 4 == 0 else 0.5), at + k * B / 2,
                                           pan=-0.35 + 0.1 * (k % 8), g=0.3)
            elif pat == 'sparse':
                for k, m in enumerate(v[::-1]):
                    tk.add(s.I.bell(m + 12, 2 * B, 0.5), at + k * 1.5 * B + 0.5 * B, pan=(-0.4, 0.4, 0)[k % 3], g=0.24)
            at += beats * B

    # ---------------- assets
    def intro(s):
        p, B = s.p, s.B; total = sum(b for _, b in p['intro']) * B; tk = Track(total + 4)
        pick = p['intro'][0][1] * B if p['intro'][0][0] == 'N' else 0
        if pick: tk.add(s.I.sweep(pick + 0.1, 200, 4000, q=0.5, vel=1.0), 0, g=0.22)     # riser into bar 1
        end = s.harmony(tk, p['intro'])
        last_beats = p['intro'][-1][1]; final = end - last_beats * B
        s.drums(tk, (final) / B, final_hit=final)
        s.melody(tk, p['melody_intro'])
        tk.add(s.I.bell(chord(p['tonic'], s.tr)[0] + 84 - 12 * ((chord(p['tonic'], s.tr)[0] + 84) > 90), 2.5, 0.7), final, pan=0.25, g=0.12)
        return s.finish(tk), final + 0.35       # cue point: speech may start just after the tonic lands

    def outro(s):
        p, B = s.p, s.B; total = sum(b for _, b in p['outro']) * B; tk = Track(total + 5)
        end = s.harmony(tk, p['outro'], last_ring=3.0)
        final = end - p['outro'][-1][1] * B
        s.drums(tk, final / B, density=0.7, final_hit=final)
        s.melody(tk, p['melody_outro'])
        return s.finish(tk), 0.0

    def sting(s):
        p = s.p; tk = Track(4.0); hit = 0.12 + p['sting'][-1][0] * s.B
        tk.add(s.I.sweep(0.5, 300, 3000, q=0.5), 0, g=0.12)
        for b, m in p['sting']:
            tk.add(s.play(p['lead'], m, 0.6, 0.85), 0.12 + b * s.B, pan=-0.3 + 0.2 * b, g=0.5)
        v = voice_lead([p['sting_chord']], tr=s.tr)[0]
        tk.add(s.I.pad(v, 1.0, 1.0, a=0.02, r=1.1, cutoff=p['pad_cut'] + 600), hit, g=0.35)
        _, _, bs = chord(p['sting_chord'], s.tr); tk.add(s.I.bass(36 + (bs - 36) % 12, 1.0, 1.0, p['bass_tone']), hit, g=0.1)
        if p['drums']: tk.add(s.I.kick(0.8), hit, g=0.45)
        return s.finish(tk), hit + 0.45

    def swoosh(s):
        f0, f1, f2, q, dur = s.p['swoosh']; tk = Track(dur + 1.5)
        tk.add(s.I.sweep(dur, f0, f1, f2, q=q), 0, g=0.6)
        return s.finish(tk, rv=(0.25, 1.2)), dur * 0.7

    def bed(s):
        p, B = s.p, s.B; beats = sum(b for _, b in p['bed']); dur = beats * B; tk = Track(dur + 4)
        s.harmony(tk, p['bed'], pad_vel=0.26, bass_vel=0.06, comp=False, last_ring=0.15)
        s.bed_pattern(tk, p['bed'])
        s.drums(tk, beats, density=0.55)
        st = reverb(tk.x, s.rng, *p['reverb'])
        n = int(dur * SR); loop = st[:n].copy(); loop[:len(st) - n] += st[n:]         # fold tail -> seamless loop
        loop = peak_eq(loop, 2500, -5.0, 0.7)        # carve the speech-presence band so words stay clear
        loop = peak_eq(loop, 280, -3.0, 0.8)         # and the voice-body band (male/female fundamentals)
        loop = filt(filt(loop, 'low', 9000), 'high', 40)
        # make the wrap continuous at sample level: 20 ms equal-power crossfade of the last 20 ms into the first 20 ms
        k = int(0.02 * SR); w = np.sin(np.linspace(0, np.pi / 2, k))[:, None]
        head = loop[:k] * w + loop[-k:] * np.cos(np.linspace(0, np.pi / 2, k))[:, None]
        loop = np.concatenate([head, loop[k:-k]])
        loop -= loop.mean(axis=0)
        return normalise(loop), 0.0

    def finish(s, tk, rv=None):
        st = reverb(tk.x, s.rng, *(rv or s.p['reverb']))
        st = np.concatenate([np.zeros((int(LEAD * SR), 2)), tail_end(st)])   # start from true silence
        return tidy(st, 0.003, 0.08)

# ---------------------------------------------------------------- user files
DEFAULT_CUE = {'intro': -1.0, 'sting': -0.5, 'swoosh': -0.4, 'outro': 0.0, 'bed': 0.0}

def import_file(name, spec, show_dir):
    spec = {'path': spec} if isinstance(spec, str) else spec
    path = spec['path'] if os.path.isabs(spec['path']) else os.path.join(show_dir, spec['path'])
    x = pk.decode(path, 2)
    e = np.abs(x).max(axis=1); idx = np.where(e > 10 ** (-50 / 20))[0]        # trim leading/trailing silence
    if len(idx): x = x[max(0, idx[0] - int(0.01 * SR)): idx[-1] + int(0.05 * SR)]
    if spec.get('max_seconds'): x = x[:int(spec['max_seconds'] * SR)]
    if name == 'bed':
        k = int(min(1.0, len(x) / SR / 4) * SR)     # equal-power crossfade the tail into the head => seamless loop
        w = np.sin(np.linspace(0, np.pi / 2, k))[:, None]
        x = np.concatenate([x[:k] * w + x[-k:] * np.cos(np.linspace(0, np.pi / 2, k))[:, None], x[k:-k]])
        x = filt(x, 'high', 25); x -= x.mean(axis=0); x = peak_eq(x, 2500, spec.get('presence_cut_db', -4.0), 0.7)
        x = normalise(x)
    else:
        x = tidy(x, 0.005, spec.get('fade_out', 0.3 if name in ('intro', 'outro') else 0.08))
    cue = spec.get('cue_point', DEFAULT_CUE[name])
    return x, (len(x) / SR + cue if cue < 0 else cue)

def build(style, out, transpose=0, bpm=None, seed=1, files=None, show_dir='.'):
    os.makedirs(out, exist_ok=True); files = files or {}; meta, report = {'style': style}, {}
    if style == 'none' and not files:
        json.dump({'style': 'none'}, open(os.path.join(out, 'music_meta.json'), 'w'), indent=1); print('music: none'); return {}
    comp = Composer(PRESETS[style], transpose, bpm, seed) if style in PRESETS else None
    if style not in PRESETS and style != 'none': pk.die(f'unknown music style {style}; choose: ' + ', '.join(PRESETS) + ', none')
    for name in ('intro', 'sting', 'swoosh', 'outro', 'bed'):
        if name in files: x, cue = import_file(name, files[name], show_dir); src = 'file'
        elif comp:
            x, cue = getattr(comp, name)(); src = 'synth:' + style
            if cue > 0: cue += LEAD
        else: continue
        p = os.path.join(out, name + '.wav'); pk.write_wav(p, x)
        meta[name] = dict(file=name + '.wav', seconds=round(len(x) / SR, 3), cue_point=round(float(cue), 3), source=src)
        report[name] = pk.analyse(x, loop=(name == 'bed')); report[name]['source'] = src
        print(f"{name:7s} {report[name]['seconds']:5.2f}s  {report[name]['lufs']:6.1f} LUFS  TP {report[name]['true_peak_dbtp']:5.1f} dBTP"
              f"  clip {report[name]['clipped_samples']}  corr {report[name].get('stereo_correlation')}  "
              f"end {report[name]['end_5ms_dbfs']} dBFS  bands {report[name]['bands_pct']}")
    json.dump(meta, open(os.path.join(out, 'music_meta.json'), 'w'), indent=1)
    json.dump(report, open(os.path.join(out, 'music_report.json'), 'w'), indent=1)
    return report

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('show', nargs='?'); ap.add_argument('--style'); ap.add_argument('--out')
    ap.add_argument('--transpose', type=int, default=0); ap.add_argument('--bpm', type=float); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--list', action='store_true'); a = ap.parse_args()
    if a.list:
        for k, v in PRESETS.items(): print(f'{k:12s} {v["desc"]}')
        print('none         no music; [INTRO]/[STING]/... cues become short pauses'); return
    if a.show:
        s = pk.load_show(a.show); m = s['music']
        build(m['style'], pk.show_paths(s)['assets'], m.get('transpose', 0), m.get('bpm'), m.get('seed', 1), m.get('files'), s['dir'])
    elif a.style and a.out: build(a.style, a.out, a.transpose, a.bpm, a.seed)
    else: ap.error('give SHOW_DIR, or --style S --out DIR, or --list')

if __name__ == '__main__':
    main()
