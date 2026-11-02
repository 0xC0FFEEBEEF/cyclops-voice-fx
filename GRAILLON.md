# Graillon 3 (Auburn Sounds) — installed 2026-09-06

Free Edition v3.2, native Linux. Downloaded from auburnsounds.com.

    ~/.lv2/Auburn Sounds Graillon 3.lv2/     <- LV2   (use this one)
    ~/.vst3/Auburn Sounds Graillon 3.vst3/   <- VST3
    ~/.clap/Auburn Sounds Graillon 3.clap    <- CLAP
    ~/cyclops-voice/docs/                    <- user guide, data sheet, cheat sheet

Verified loading in Carla: LV2 mono (1in/1out) and stereo (2in/2out), 65
parameters; VST3 also loads. Carla finds it in `~/.lv2` with no `LV2_PATH` set.

## Why this matters for the Cyclops work

Graillon has, as first-class controls, the exact things I hand-built in
`cyclops.py` — and its versions are better:

| my knob | Graillon parameter |
|---|---|
| `--pitch` | p15 Pitch Shift |
| `--form`  | p51 Formant Shift, p16 Formant Amount |
| `--sub`   | p3 1st Sub Oct Mix, p1 2nd Sub Oct Mix (**and** sub-5ths p4/p2) |
| `--auto` / `--scale` | p17 Corr. Amount, p11 Quantize, p18 Smooth, p22–p33 per-note Allow |
| (none) | p10 Reduce = bitcrusher, p57 Chorus, p55 Compressor, p56 Gate |

The sub-octave generator is the headline: measurements of the real Cyclops
showed a big sub-100 Hz layer that tracks the voice, and Graillon generates
that natively — including a sub-fifth, which I never built.

## Using it in LMMS 1.2.2 — read this first

**Graillon cannot go in LMMS's effect chain.** LMMS 1.2.2 on Arch only supports
these effect formats:

    libladspaeffect.so  libpeakcontrollereffect.so  libvsteffect.so

i.e. LADSPA and *Windows* VST. There is no LV2 effect host. And the Arch package
ships **no `RemoteVstPlugin*.exe`**, which is the Wine-side bridge Windows VSTs
require — so the Windows-VST route is dead on this build too.

What does work: LMMS bundles Carla (`libcarlarack.so`, `libcarlapatchbay.so`),
and Carla hosts LV2 natively.

### Route A — inside LMMS, via Carla Patchbay
1. Instrument browser → drag **Carla Patchbay** onto a track.
2. Open its GUI, then *Add Plugin*.
3. Inside Carla add an **Audio File** plugin, point it at your TTS wav.
4. *Add Plugin* again → search `Graillon` → add it.
5. In the patchbay, wire Audio File out → Graillon in → Carla out.

Everything happens inside Carla; LMMS is only the shell. Which is why:

### Route B — just use Carla standalone (recommended)
For processing TTS files this is strictly simpler — LMMS adds nothing here.

    carla

Then *Add Plugin* → **Audio File** (your wav) → *Add Plugin* → **Graillon 3**,
wire them up, hit play. Dial it in by ear, save the Carla project as a preset.

### Route C — keep using cyclops.py
Still the best fit for batch: one command, no GUI, no realtime pass.

## What did NOT work

- **`lv2apply` segfaults** (exit 139) on Graillon. It doesn't provide the
  `options#options` feature the plugin lists as required. `lilv-tools` is
  installed but is not a viable host for this plugin. If a scripted offline
  Graillon pass is wanted later, it needs a real host (Carla/JACK, or Ardour's
  offline bounce), not `lv2apply`.
- `~/.vst/Auburn Sounds Graillon 3-64.dll` — I extracted this Windows VST2 from
  the installer before finding that LMMS has no Wine bridge on this build.
  **It is unused and can be deleted.** Native LV2/VST3/CLAP are all present, so
  there is no reason to bridge a Windows build.

## Note on the name

`pacman -S helm` installs the **Kubernetes package manager**, not Matt Tytel's
synth. The synth is `helm-synth-{lv2,vst,standalone}` in the AUR. Not installed —
Graillon replaced the need for it.
