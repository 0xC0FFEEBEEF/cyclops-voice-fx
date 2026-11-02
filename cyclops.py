#!/usr/bin/env python3
"""
Cyclops voice processor (Subnautica-style).

Chain, in order:
  1. cleanup + de-ess/darken     (TTS is too bright; real Cyclops is dark)
  2. pitch/formant shift down    (bigger body)
  3. autotune layer (PSOLA)      (some syllables snap to a scale -> "sings")
  4. sub-octave layer            (the "massive vessel" bottom octave)
  5. flanger                     (the whirring-fan robot shimmer)
  6. convolution reverb          (echoing around a huge hull)
  7. compressor + soft-clip      ("louder than loud")

Usage:  cyclops.py in.wav out.wav [--preset cyclops|subtle|monster] [--key C] ...
"""
import argparse, sys
import numpy as np
from scipy.signal import butter, sosfilt, sosfiltfilt, fftconvolve, resample_poly

SR = 48000

# ---------------------------------------------------------------- io helpers
def load(path, sr=SR):
    import soundfile as sf
    x, fs = sf.read(path, always_2d=True)
    x = x.mean(1).astype(np.float64)
    if fs != sr:
        from math import gcd
        g = gcd(int(fs), sr)
        x = resample_poly(x, sr // g, int(fs) // g)
    m = np.abs(x).max()
    return x / m if m > 0 else x

def save(path, x, sr=SR):
    import soundfile as sf
    sf.write(path, np.clip(x, -1, 1), sr, subtype="PCM_24")

def _sos(kind, f, sr=SR, order=4):
    f = np.atleast_1d(f) / (sr / 2)
    return butter(order, f if len(f) > 1 else f[0], kind, output="sos")

def hp(x, f, o=4): return sosfilt(_sos("highpass", f, order=o), x)
def lp(x, f, o=4): return sosfilt(_sos("lowpass",  f, order=o), x)
def bp(x, lo, hi, o=4): return sosfilt(_sos("bandpass", [lo, hi], order=o), x)

def env(x, sr=SR, ms=30):
    w = max(1, int(sr * ms / 1000))
    return np.sqrt(np.convolve(x**2, np.ones(w) / w, "same"))

def shelf(x, freq, gain_db, kind="high"):
    """Simple shelving via band split + gain."""
    g = 10 ** (gain_db / 20)
    hi = x - lp(x, freq)
    lo = lp(x, freq)
    return (lo + hi * g) if kind == "high" else (lo * g + hi)

# ------------------------------------------------------- 1. cleanup / darken
def darken(x, deess_db=-4.0, tilt_db=2.0, top=9000):
    x = hp(x, 65, 2)
    x = shelf(x, 4000, deess_db, "high")      # kill TTS sibilance
    x = lp(x, top, 4)                          # band-limit the top
    body = bp(x, 500, 2000)                    # restore chest/presence
    return x + body * (10 ** (tilt_db / 20) - 1)

# --------------------------------------------------- 2. pitch/formant shift
def pitch_shift(x, semitones, sr=SR, formant_shift=True):
    """Shift pitch down/up. formant_shift=True moves formants too (bigger body).

    Direction check: semitones=-2 must LOWER f0 by a factor 2**(-2/12)=0.891.
    Method: resample (moves pitch+formants together, changes duration), then
    time-stretch back to the original duration.
    """
    if abs(semitones) < 0.01:
        return x
    import librosa
    r = 2.0 ** (semitones / 12.0)              # target pitch ratio (<1 = down)
    if not formant_shift:
        return librosa.effects.pitch_shift(
            x.astype(np.float32), sr=sr, n_steps=semitones).astype(np.float64)
    n_new = int(round(len(x) / r))              # r<1 -> longer -> lower pitch
    y = np.interp(np.linspace(0, len(x) - 1, n_new), np.arange(len(x)), x)
    y = librosa.effects.time_stretch(y.astype(np.float32), rate=n_new / len(x))
    y = np.asarray(y, dtype=np.float64)
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    return y[:len(x)]


def cep_env(logm, n_fft, lifter=80):
    """Spectral envelope by cepstral liftering (validated in formant_shift)."""
    cep = np.fft.irfft(logm, n=n_fft, axis=0)
    cep[lifter:n_fft - lifter + 1, :] = 0.0
    return np.fft.rfft(cep, n=n_fft, axis=0).real


def formant_shift(x, factor, sr=SR, n_fft=2048, hop=512, lifter=80):
    """Move the spectral envelope WITHOUT touching pitch.

    factor < 1 lowers the formants  -> larger resonant body ("bigger throat")
    factor > 1 raises them          -> smaller, tinnier

    This is the knob that decides how *big the thing talking* sounds, which is
    separate from how low it is. Pitch alone just sounds like a slowed person.

    The envelope is extracted by cepstral liftering: keeping the first `lifter`
    cepstral coefficients resolves spectral detail down to ~sr/lifter Hz (600 Hz
    here), which keeps formants but discards the f0 harmonic comb. Smoothing the
    log-spectrum with a wide moving average instead flattens the formants away
    and makes this whole function a no-op.
    """
    if abs(factor - 1.0) < 0.005:
        return x
    import librosa
    S = librosa.stft(x.astype(np.float32), n_fft=n_fft, hop_length=hop)
    mag, ph = np.abs(S), np.angle(S)
    logm = np.log(mag + 1e-9)

    env = cep_env(logm, n_fft, lifter)

    nb = logm.shape[0]
    src = np.arange(nb)
    warp = np.clip(src / factor, 0, nb - 1)            # env2[f] = env[f/factor]
    env2 = np.empty_like(env)
    for i in range(env.shape[1]):
        env2[:, i] = np.interp(warp, src, env[:, i])

    mag2 = np.exp(logm - env + env2)                   # swap envelopes
    y = librosa.istft(mag2 * np.exp(1j * ph), hop_length=hop, length=len(x))
    return np.asarray(y, dtype=np.float64)


# ------------------------------------------------------- 3. autotune (PSOLA)
SCALES = {"maj": [0,2,4,5,7,9,11], "min": [0,2,3,5,7,8,10],
          "chrom": list(range(12)), "fifths": [0,7], "triad_min": [0,3,7]}
NOTES = {n: i for i, n in enumerate(
    ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"])}

def _pitch_marks(x, f0, times, sr=SR):
    """Glottal pulse positions, walked forward one period at a time."""
    marks, t = [], float(times[0])
    end = len(x) / sr
    xl = lp(x, 900, 4)
    while t < end:
        i = int(t * sr)
        f = np.interp(t, times, f0)
        if not np.isfinite(f) or f <= 0:
            t += 0.005
            continue
        T = sr / f
        w = int(T * 0.4)
        a, b = max(0, i - w), min(len(x), i + w)
        if b - a > 2:
            i = a + int(np.argmax(xl[a:b]))     # snap to local peak
        marks.append(i)
        t = i / sr + T
    return np.array(sorted(set(marks)))

def psola(x, f0, times, target_f0, sr=SR):
    """Time-domain PSOLA: re-pitch to target_f0 while keeping duration."""
    marks = _pitch_marks(x, f0, times, sr)
    if len(marks) < 3:
        return x
    out = np.zeros(len(x) + sr)
    periods = np.diff(marks)
    periods = np.append(periods, periods[-1])
    pos = float(marks[0])
    while pos < len(x):
        k = int(np.argmin(np.abs(marks - pos)))     # nearest analysis pulse
        c, P = marks[k], int(periods[k])
        if P < 8:
            pos += 8
            continue
        a, b = max(0, c - P), min(len(x), c + P)
        seg = x[a:b] * np.hanning(b - a)
        o = int(pos) - (c - a)
        if o < 0:
            seg, o = seg[-o:], 0
        n = min(len(seg), len(out) - o)
        if n > 0:
            out[o:o + n] += seg[:n]
        tf = np.interp(pos / sr, times, target_f0)
        pos += sr / tf if np.isfinite(tf) and tf > 0 else P
    return out[:len(x)]

def autotune(x, sr=SR, key="C", scale="min", strength=0.85,
             syllable_prob=0.55, seed=7):
    """Snap pitch to a scale, but only on *some* syllables (the fun part)."""
    import librosa
    f0, voiced, _ = librosa.pyin(x.astype(np.float32), sr=sr, fmin=65, fmax=400,
                                 frame_length=2048, hop_length=256)
    times = librosa.times_like(f0, sr=sr, hop_length=256)
    if not np.any(voiced):
        return x, np.zeros_like(x)
    f0f = np.where(np.isfinite(f0), f0, 0.0)
    # interpolate through unvoiced so PSOLA can walk continuously
    good = f0f > 0
    if good.sum() < 4:
        return x, np.zeros_like(x)
    f0i = np.interp(times, times[good], f0f[good])

    root = NOTES[key.upper().replace("MAJ","").replace("MIN","").strip()] if key[:1].upper() in NOTES else 0
    degrees = SCALES.get(scale, SCALES["min"])
    midi = 69 + 12 * np.log2(f0i / 440.0)
    rel = midi - root
    oct_, semi = np.floor(rel / 12), rel % 12
    grid = np.array(degrees)
    snapped = grid[np.argmin(np.abs(semi[:, None] - grid[None, :]), axis=1)]
    tgt_midi = root + oct_ * 12 + snapped

    # choose which syllables get tuned: split voiced runs into groups
    rng = np.random.default_rng(seed)
    mask = np.zeros(len(times))
    runs, s = [], None
    for i, v in enumerate(good):
        if v and s is None: s = i
        elif not v and s is not None: runs.append((s, i)); s = None
    if s is not None: runs.append((s, len(good)))
    for a, b in runs:
        if rng.random() < syllable_prob:
            mask[a:b] = 1.0
    # smooth the mask so tuning fades in/out instead of clicking
    k = np.hanning(21); k /= k.sum()
    mask = np.convolve(mask, k, "same")

    blend = strength * mask
    tgt_f0 = 440.0 * 2 ** ((midi * (1 - blend) + tgt_midi * blend - 69) / 12)
    tuned = psola(x, f0i, times, tgt_f0, sr)
    # gate the tuned layer to where we actually tuned
    g = np.interp(np.arange(len(x)) / sr, times, mask)
    return tuned, g


# ------------------------------------------------------------- 3b. vocoder
def make_carrier(x, sr=SR, chord=(0,), noise=0.18, hop=256, seed=1):
    """Synth carrier that FOLLOWS the voice's own melody.

    A fixed-pitch carrier (the classic robot vocoder) throws away the prosody
    and sounds dead. Tracking f0 keeps the delivery and just swaps the timbre.
    `chord` adds extra saws at semitone offsets -> the harmonising layer.
    Noise fills unvoiced frames so consonants survive.
    """
    import librosa
    f0, vflag, vprob = librosa.pyin(x.astype(np.float32), sr=sr, fmin=60, fmax=400,
                                    frame_length=2048, hop_length=hop)
    t = librosa.times_like(f0, sr=sr, hop_length=hop)
    good = np.isfinite(f0)
    if good.sum() < 4:
        return np.zeros_like(x)
    f0i = np.interp(t, t[good], f0[good])
    vp = np.nan_to_num(vprob, nan=0.0)
    n = len(x)
    tt = np.arange(n) / sr
    f = np.interp(tt, t, f0i)
    voiced = np.interp(tt, t, vp)

    car = np.zeros(n)
    for iv in chord:
        ph = 2 * np.pi * np.cumsum(f * 2 ** (iv / 12.0)) / sr
        car += 2.0 * ((ph / (2 * np.pi)) % 1.0) - 1.0      # sawtooth
    car /= max(1, len(chord))
    car = lp(car, 10000, 4)                                # tame aliasing
    rng = np.random.default_rng(seed)
    nz = rng.standard_normal(n)
    return car * voiced + nz * (noise + 0.55 * (1 - voiced))


def vocode(voice, carrier, sr=SR, n_fft=1024, hop=256, lifter=60):
    """Cross-synthesis: the voice's spectral envelope on the carrier's spectrum."""
    import librosa
    Sv = librosa.stft(voice.astype(np.float32), n_fft=n_fft, hop_length=hop)
    Sc = librosa.stft(carrier.astype(np.float32), n_fft=n_fft, hop_length=hop)
    m = min(Sv.shape[1], Sc.shape[1])
    Sv, Sc = Sv[:, :m], Sc[:, :m]
    lv = np.log(np.abs(Sv) + 1e-9)
    lc = np.log(np.abs(Sc) + 1e-9)
    ev = cep_env(lv, n_fft, lifter)          # what the voice says
    ec = cep_env(lc, n_fft, lifter)          # flatten the carrier
    mag = np.exp(lc - ec + ev)
    y = librosa.istft(mag * np.exp(1j * np.angle(Sc)), hop_length=hop,
                      length=len(voice))
    y = np.asarray(y, dtype=np.float64)
    p = np.abs(y).max()
    return y / p if p > 0 else y


# ------------------------------------------------------ 4. sub-octave layer
def sub_octave(x, sr=SR, amount=0.55, top=140):
    """Octave-down layer -> the 'massive vessel' bottom. Tracks the voice env."""
    import librosa
    d = librosa.effects.pitch_shift(x.astype(np.float32), sr=sr, n_steps=-12)
    d = lp(np.asarray(d, dtype=np.float64), top, 4)
    d = hp(d, 35, 2)
    e = env(x, sr, 40)
    e /= e.max() + 1e-9
    e = np.clip((e - 0.04) / 0.5, 0, 1) ** 0.7       # gate out the silences
    return d * e * amount

# ------------------------------------------------------------- 5. flanger
def flanger(x, sr=SR, depth_ms=3.0, rate=0.25, feedback=0.0, mix=0.5):
    n = len(x)
    t = np.arange(n) / sr
    d = (1.0 + (depth_ms - 1.0) * 0.5 * (1 - np.cos(2*np.pi*rate*t))) * sr/1000
    out = np.zeros(n); buf = np.zeros(n)
    idx = np.arange(n) - d
    i0 = np.floor(idx).astype(int); fr = idx - i0
    ok = i0 > 1
    # feedback comb, computed with a short recursive pass
    y = x.copy()
    prev = np.zeros(n)
    for _ in range(2):                                   # 2 iterations approximates fb
        s = np.zeros(n)
        s[ok] = y[i0[ok]] * (1 - fr[ok]) + y[i0[ok] + 1] * fr[ok]
        y = x + feedback * s
        prev = s
    return (1 - mix) * x + mix * prev

# -------------------------------------------------------------- 6. reverb
def make_ir(sr=SR, rt60=1.1, predelay_ms=18, dark=2200, seed=3):
    """Smooth, diffuse, dark tail.

    No sparse early-reflection taps: discrete taps at fixed delays comb-filter
    the voice and produce the hollow 'tin can / saucepan' ring. A dense noise
    tail with a short fade-in stays diffuse instead.
    """
    rng = np.random.default_rng(seed)
    n = int(sr * rt60)
    t = np.arange(n) / sr
    ir = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    build = np.clip(t / 0.012, 0, 1)            # soft onset, no slapback click
    ir *= build
    ir = lp(ir, dark, 4)
    ir = hp(ir, 160, 2)                          # keep reverb out of the sub
    pre = np.zeros(int(sr * predelay_ms / 1000))
    ir = np.concatenate([pre, ir])
    return ir / (np.abs(ir).max() + 1e-9)


def reverb(x, wet=0.30, **kw):
    ir = make_ir(**kw)
    w = fftconvolve(x, ir)[:len(x)]
    w /= np.abs(w).max() + 1e-9
    return (1 - wet) * x + wet * w

# ------------------------------------------------ 7. compressor / soft clip
def compress(x, sr=SR, thresh_db=-20, ratio=6.0, attack_ms=5, release_ms=90,
             makeup_db=6.0):
    e = env(x, sr, 5) + 1e-9
    db = 20 * np.log10(e)
    over = np.maximum(0, db - thresh_db)
    gr = -over * (1 - 1 / ratio)
    a = np.exp(-1 / (sr * attack_ms / 1000))
    r = np.exp(-1 / (sr * release_ms / 1000))
    s = np.zeros_like(gr); p = 0.0
    for i, v in enumerate(gr):                 # smoothed gain reduction
        c = a if v < p else r
        p = c * p + (1 - c) * v
        s[i] = p
    return x * 10 ** ((s + makeup_db) / 20)

def softclip(x, drive=1.4):
    return np.tanh(x * drive) / np.tanh(drive)

# ---------------------------------------------------------------- presets
CHORDS = {"uni": (0,), "oct": (0, -12), "fifth": (0, 7),
          "min": (0, 3, 7), "maj": (0, 4, 7), "min7": (0, 3, 7, 10),
          "wide": (0, -12, 7)}

PRESETS = {
    # VOICE FIRST: wet/fl default to 0. Get the voice right, add space later.
    # name:      pitch  form   sub   auto  ap    fl   wet  drive  deess
    "vocoder":  dict(pitch=-3.0, form=0.90, sub=0.55, auto=0.00, ap=0.00, fl=0.0, wet=0.0, drive=1.2, deess=-4.0, voc=0.45, chord="uni"),
    "choir":    dict(pitch=-3.0, form=0.90, sub=0.55, auto=0.00, ap=0.00, fl=0.0, wet=0.0, drive=1.2, deess=-4.0, voc=0.55, chord="min"),
    "voice":    dict(pitch=-3.0, form=0.92, sub=0.55, auto=0.00, ap=0.00, fl=0.0, wet=0.0, drive=1.2, deess=-4.0),
    "cyclops":  dict(pitch=-3.0, form=0.90, sub=0.60, auto=0.60, ap=0.45, fl=0.0, wet=0.0, drive=1.3, deess=-4.0),
    "big":      dict(pitch=-4.0, form=0.84, sub=0.75, auto=0.00, ap=0.00, fl=0.0, wet=0.0, drive=1.4, deess=-5.0),
    "clean":    dict(pitch=-2.0, form=0.95, sub=0.45, auto=0.00, ap=0.00, fl=0.0, wet=0.0, drive=1.2, deess=-3.0),
}

def process(x, p, key="C", scale="min", seed=7, sr=SR):
    x = darken(x, deess_db=p["deess"])
    x = pitch_shift(x, p["pitch"], sr, formant_shift=False)
    x = formant_shift(x, p.get("form", 1.0), sr)
    x /= np.abs(x).max() + 1e-9

    dry = x.copy()
    if p["auto"] > 0:
        tuned, g = autotune(x, sr, key, scale, p["auto"], p["ap"], seed)
        tuned /= np.abs(tuned).max() + 1e-9
        # both voices at once where tuned -> the harmonising/doubling effect
        x = dry * (1 - 0.45 * g) + tuned * 0.75 * g

    if p.get("voc", 0) > 0:
        ch = CHORDS.get(p.get("chord", "uni"), (0,))
        v = vocode(dry, make_carrier(dry, sr, chord=ch), sr)
        x = x * (1 - p["voc"]) + v * p["voc"] * 0.9

    x = x + sub_octave(dry, sr, p["sub"])
    x /= np.abs(x).max() + 1e-9
    if p["fl"] > 0:  x = flanger(x, sr, mix=p["fl"])
    if p["wet"] > 0: x = reverb(x, wet=p["wet"])
    x = compress(x, sr)
    x = softclip(x, p["drive"])
    return x / (np.abs(x).max() + 1e-9) * 0.89

def main():
    ap = argparse.ArgumentParser(description="Subnautica Cyclops voice processor")
    ap.add_argument("infile"); ap.add_argument("outfile")
    ap.add_argument("--preset", default="cyclops", choices=list(PRESETS))
    ap.add_argument("--key", default="C"); ap.add_argument("--scale", default="min",
                    choices=list(SCALES))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--chord", default=None, choices=list(CHORDS))
    for k in ("pitch","form","sub","auto","ap","fl","wet","drive","deess","voc"):
        ap.add_argument(f"--{k}", type=float, default=None)
    a = ap.parse_args()
    p = dict(PRESETS[a.preset])
    if a.chord is not None: p["chord"] = a.chord
    for k in p:
        if k == "chord": continue
        v = getattr(a, k, None)
        if v is not None: p[k] = v
    x = load(a.infile)
    y = process(x, p, a.key, a.scale, a.seed)
    save(a.outfile, y)
    print(f"{a.infile} -> {a.outfile}  [{a.preset}] {p}")

if __name__ == "__main__":
    main()
