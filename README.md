# Cyclops voice (Subnautica) — TTS post-processor

    ~/cyclops-voice/cyclops in.wav out.wav --preset cyclops

## Why your TTS didn't sound like it

Measured against 5 real Cyclops clips in your Downloads, plus the "after"
segments of `subnautica-ab.wav`:

| Band | Real Cyclops | Your audio(17) | Gap |
|---|---|---|---|
| 0–100 Hz | 9.3 % | **1.6 %** | **+7.7 dB** |
| 500–2000 Hz | 31.2 % | 19.9 % | +2 dB |
| 4–8 kHz | 8.8 % | **20.4 %** | **−3.7 dB** |
| centroid | ~1500 Hz | **2285 Hz** | far too bright |

Two things dominate, and neither is on the Reddit list:

1. **You are missing the whole bottom octave.** The Cyclops has ~9 % of its
   energy under 100 Hz. Critically, that sub-bass *starts and stops with the
   voice* (correlation 0.556; level 0.028 when silent vs 0.448 when speaking),
   so it is not engine-room ambience in the recording — it is a sub-octave
   layer in the voice chain. This is the "massive vessel" body, and it is the
   single biggest reason a raw TTS never lands.
2. **You are far too bright.** TTS is trained to sound crisp; the Cyclops is a
   dark, band-limited shipboard PA. Your 4–8 kHz is 2.3× too hot.

Get those two right and it reads as Cyclops even before the fun effects.
Score against the target profile went **48.8 → 5.3** (lower = closer) — but see
the bug note below: that score was hit *while the pitch shift was inverted*,
which is exactly why a good number and a bad-sounding result coexisted.

## The chain

Applied in this order (order matters — sub-octave is generated from the
*pre-autotune* signal so tuning artifacts don't get an octave-down copy):

1. **darken** — high-shelf cut above 4 kHz, lowpass ~9 kHz, +2 dB at 500–2000 Hz
2. **pitch/formant shift** −2 semitones (formants move too → bigger, not chipmunk)
3. **autotune (TD-PSOLA)** — snaps to a scale, but only on a *random subset of
   syllables* (`--ap`), which is the "you're never sure if a line will sing"
   behaviour. Where it tunes, dry and tuned are mixed together → the
   harmonising/doubling you hear in the game.
4. **sub-octave layer** — −12 st, lowpassed to 140 Hz, gated to the voice envelope
5. **flanger** — modulated comb, the whirring-fan shimmer
6. **convolution reverb** — synthesised dark metallic-hull IR, RT60 1.6 s, 22 ms predelay
7. **compressor + soft clip** — the "louder than loud" crush

## Presets

| preset | character | target error |
|---|---|---|
| `cyclops` | the match — use this | **5.3** |
| `clean` | same tone, no autotune (best intelligibility for long announcements) | 11.9 |
| `subtle` | lightly flavoured, still obviously your TTS voice | 23.3 |
| `monster` | much deeper/wetter; cool but smears speech | 20.9 |

## Knobs

Any preset value can be overridden:

    cyclops in.wav out.wav --preset cyclops --sub 0.7 --wet 0.35 --auto 0.6

| flag | does | sane range |
|---|---|---|
| `--pitch` | semitones down | −1 … −4 |
| `--sub` | sub-octave layer level | 0.3 … 0.9 |
| `--auto` | autotune strength | 0 … 1 |
| `--ap` | fraction of syllables that get tuned | 0 … 1 |
| `--fl` | flanger mix | 0 … 0.6 |
| `--wet` | reverb amount | 0.15 … 0.4 |
| `--drive` | soft-clip crush | 1.0 … 2.0 |
| `--deess` | dB cut above 4 kHz | −2 … −6 |
| `--key` / `--scale` | tuning target (`min`, `maj`, `chrom`, `fifths`, `triad_min`) | |
| `--seed` | reshuffles *which* syllables sing | any int |

Tips:
- Long informational lines: `--preset clean` or `--ap 0.3`. Heavy autotune on a
  long sentence gets tiring and hurts intelligibility.
- Short alert stingers: `--ap 0.9 --scale triad_min` sounds great.
- `--seed` is the cheapest way to get variation across many generated lines.

## Auditioning

    ~/cyclops-voice/audition in.wav

Renders 15 variants, each announced by number (via espeak-ng) so you can pick
by ear in one pass, into `~/cyclops-voice/audition-out/`:

- `SWEEP.mp3` — everything stitched together, ~2.5 min. Just play it.
- `01.wav` … `15.wav` — the individual clips, if you want to loop one.

Takes ~45 s for a 7 s input.

## Notes

- Uses its own venv at `~/cyclops-voice/venv` (numpy/scipy/soundfile/librosa).
- `pedalboard` is **not** usable on this machine — the wheel SIGILLs on your
  Ryzen 4500U. Everything here is numpy/scipy, so it stays portable.
- ~9 s to process 7.5 s of audio, single-threaded.
- Verified: no NaN/Inf, no DC offset, 1 clipped sample. Autotune does *not*
  hurt intelligibility (envelope correlation 0.582 with it, 0.576 without —
  the smearing is the reverb, which is authentic).

## Status / what's not done yet

- Integration with the Cube Pro is **not** started (deferred at your request).
- No local TTS on this machine — only espeak-ng, which is used for the audition
  labels, not for voice. `audio(17).wav` is 24 kHz mono, i.e. Kokoro's native
  output, so you're generating those elsewhere. When you want the full local
  loop, installing Kokoro or Piper here gets it to one command.


## Bugs found and fixed (2026-09-06, second pass)

Three real defects, all found only after the user said it sounded wrong:

1. **The pitch shift was fully inverted.** `--pitch -4` produced 184.9 Hz from a
   146 Hz input instead of 115.9 Hz — it went *up* four semitones. Cause:
   `time_stretch(rate=2**(st/12))` followed by resampling back to the original
   length nets a pitch change of the reciprocal. Fixed by resampling first
   (`n_new = len(x)/r`) and time-stretching back. Now exact: −5→109.4 Hz,
   −4→115.9, −3→122.1, −2→130.9, all with duration ratio 1.000.

   The band-energy score never caught this because the sub-octave layer and the
   EQ satisfied it regardless of which way the voice went. **A metric that can
   be satisfied by the wrong mechanism is not a test.** Direction is now
   asserted directly against measured f0.

2. **The reverb IR rang like a saucepan.** Discrete early-reflection taps at
   11/19/29/41/57 ms with alternating polarity are textbook comb filtering.
   Cepstral analysis: strongest peak 82× the median (discrete echo) → 7.2×
   (diffuse) after replacing them with a dense tail and a 12 ms soft onset.
   Note that a raw spectral-ripple metric said the new IR was *worse* (31 dB vs
   26 dB) — ripple is meaningless for noise IRs; the cepstrum is the right tool.

3. **`formant_shift` was a no-op.** Envelope smoothing was 25 bins ≈ 1172 Hz
   wide, which erases the formants it is supposed to move. A synthetic control
   (known formants 500/1500/2500 Hz) showed factor 0.9 leaving them at
   495/1540/2502. Rewritten with cepstral liftering (`lifter=80` → ~600 Hz
   resolution, keeps formants, discards the f0 comb). Verified against the
   control: 0.95→0.952, 0.90→0.905, 0.84→0.838, 0.80→0.809, pitch unmoved.

   LPC formant estimation disagreed at factor 0.84 — it latches onto harmonics
   when F1 gets low. Measuring the cepstral envelope directly is reliable.

## Current approach: voice first

Reverb, echo and flanger now default to **off** (`wet=0`, `fl=0`). Get the voice
right, then add space. `./voice-test in.wav` sweeps pitch and formant
independently — including pitch-only and formant-only clips, so you can hear
what each knob does on its own.

Measured f0 of the voice-only sweep (real Cyclops sits at 110–135 Hz):

| variant | f0 | sub % | centroid |
|---|---|---|---|
| your original | 146.0 | 2.1 | 2494 |
| `--pitch -3 --form 0.90` | 123.5 | 6.6 | 1419 |
| `--pitch -3 --form 0.84` | 123.8 | 7.0 | 1320 |
| `--pitch -4 --form 0.88` | 113.9 | 10.2 | 1397 |
| `--pitch -3 --form 0.90 --sub 0.6` | 116.5 | 12.3 | 1306 |

`--form` is the new knob and it is the important one: it sets how *big the
thing talking* is, independently of pitch. Pitch alone just sounds like a
slowed-down person, which is part of why the first pass failed.

## About this copy

A public snapshot of my private working repo. More of my projects: [nbdevlab.com](https://www.nbdevlab.com).


## About this copy

A public snapshot of my private working repo. More of my projects: [nbdevlab.com](https://www.nbdevlab.com).

## License

GNU General Public License v3.0 or later, see [`LICENSE`](LICENSE).

<sub>Preparing this public copy from my private repo was done with help from Claude.</sub>
