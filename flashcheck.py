#!/usr/bin/env python3
"""
flashcheck - photosensitivity screening for video files
=======================================================

Screens a video for content that is likely to trigger photosensitive
seizures, and reports two independent risk factors:

  1. Large-area luminance flashes
     A transition counts as a flash when the mean luminance changes by
     more than `flash_delta` AND more than `flash_area` of the frame is
     affected. The tool reports the largest number of flashes falling
     within any one-second window. More than three is where risk is
     generally considered significant.

  2. Regular-pattern strength
     Striped and checkerboard patterns are a separate trigger mechanism
     from flashing. The tool takes the 2-D Fourier transform of sampled
     frames and reports what fraction of the spectral energy sits in the
     strongest peaks. Concentrated energy means strong periodic
     structure.

Both checks are inspired by the thresholds used in ITU-R BT.1702 and
WCAG 2.3.1.

IMPORTANT
---------
This is a screening heuristic, NOT a certified assessment. It is not an
implementation of the Harding test and does not replace one. It has no
red-flash test and no luminance calibration - see README for the full
list of limitations. A "low risk" result is not a clearance.

Usage
-----
    python flashcheck.py video.mp4
    python flashcheck.py video.mp4 --json report.json
    python flashcheck.py video.mp4 --plot report.png

Requires ffmpeg on PATH, plus numpy.
The --plot option additionally needs matplotlib.
"""

import argparse
import json
import os
import subprocess
import sys

import numpy as np


# ----------------------------------------------------------------------
# Thresholds
# ----------------------------------------------------------------------
FLASH_DELTA = 0.10      # minimum change in mean luminance
FLASH_AREA = 0.25       # minimum fraction of frame that must change
FLASH_PER_SEC = 3       # flashes per second above which risk is significant
PATTERN_HIGH = 0.35     # spectral concentration considered high
PATTERN_MODERATE = 0.15
MIN_CONTRAST = 0.04     # below this a frame is treated as featureless


# ----------------------------------------------------------------------
# Video loading
# ----------------------------------------------------------------------
def probe(path):
    """Return (width, height, fps) for the first video stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "csv=p=0", path],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit(f"could not read video: {path}")
    w, h, rate = out.stdout.strip().split(",")[:3]
    num, den = rate.split("/")
    den = float(den) or 1.0
    return int(w), int(h), float(num) / den


def load_frames(path, width=320):
    """Decode the video to small greyscale frames in memory.

    Frames are downscaled because both measurements are about
    large-scale luminance and coarse periodic structure; full resolution
    would cost memory without changing the result meaningfully.
    """
    w, h, fps = probe(path)
    sw = width
    sh = max(1, int(round(h * sw / w)))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"scale={sw}:{sh},format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True).stdout
    n = len(raw) // (sw * sh)
    if n == 0:
        sys.exit("no frames decoded - is this a video file?")
    frames = np.frombuffer(raw[:n * sw * sh], np.uint8)
    frames = frames.reshape(n, sh, sw).astype(np.float32) / 255.0
    return frames, fps


# ----------------------------------------------------------------------
# Measurements
# ----------------------------------------------------------------------
def find_flashes(frames, delta=FLASH_DELTA, area=FLASH_AREA):
    """Detect large-area luminance flashes.

    A *flash* in the published guidance is a light-dark-light cycle,
    not a single transition. Counting every transition double-counts:
    a 2 Hz square wave produces four transitions per second but only
    two flashes. This function therefore counts only the dark->light
    transitions.

    Returns a boolean array of length len(frames) - 1.
    """
    means = frames.reshape(len(frames), -1).mean(axis=1)
    d_mean = np.diff(means)

    changed_area = np.empty(len(frames) - 1, np.float32)
    for i in range(len(frames) - 1):
        changed_area[i] = float(
            (np.abs(frames[i + 1] - frames[i]) > delta).mean())

    # only rising transitions -> one count per light-dark-light cycle
    return (d_mean > delta) & (changed_area > area)


def busiest_window(flash, fps):
    """Largest number of flashes inside any one-second window."""
    win = max(1, int(round(fps)))
    kernel = np.ones(win, np.float32)
    counts = np.convolve(flash.astype(np.float32), kernel, mode="same")
    return counts


def pattern_strength(frames, samples=16, top_peaks=20):
    """Fraction of spectral energy held by the strongest Fourier peaks.

    High values mean the image is dominated by regular periodic
    structure (stripes, gratings, checkerboards).
    """
    idx = np.linspace(0, len(frames) - 1, samples).astype(int)
    scores = []
    for i in idx:
        f = frames[i] - frames[i].mean()
        # A near-uniform frame has no pattern, but its spectrum is pure
        # numerical noise - and the strongest peaks of near-zero noise
        # still form a large fraction of a near-zero total, which
        # produced spurious "moderate" scores on flat gradients.
        if float(frames[i].std()) < MIN_CONTRAST:
            scores.append(0.0)
            continue
        if not np.any(f):
            continue
        spec = np.abs(np.fft.fftshift(np.fft.fft2(f))) ** 2
        cy, cx = spec.shape[0] // 2, spec.shape[1] // 2
        spec[cy - 2:cy + 3, cx - 2:cx + 3] = 0.0     # drop DC
        total = spec.sum()
        if total <= 0:
            continue
        peaks = np.sort(spec.ravel())[-top_peaks:].sum()
        scores.append(float(peaks / total))
    return float(np.mean(scores)) if scores else 0.0



# ----------------------------------------------------------------------
# Visual report
# ----------------------------------------------------------------------
def write_plot(path, frames, fps, flash, counts, score, out_png):
    """Render a one-page visual report of both measurements."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n[plot] matplotlib not installed - skipping "
              "(pip install matplotlib)")
        return

    means = frames.reshape(len(frames), -1).mean(axis=1)
    t_frame = np.arange(len(means)) / fps
    t_pair = np.arange(len(flash)) / fps

    bg = "#101014"
    fg = "#e8e8ec"
    accent = "#ffb86b"
    warn = "#ff5c5c"

    fig, ax = plt.subplots(3, 1, figsize=(11, 8.2), facecolor=bg,
                           gridspec_kw={"height_ratios": [2, 2, 1.5]})
    for a in ax:
        a.set_facecolor(bg)
        for sp in a.spines.values():
            sp.set_color("#3a3a44")
        a.tick_params(colors="#9a9aa6", labelsize=9)
        a.grid(alpha=0.12, color=fg)

    # --- 1: mean luminance with flashes marked ---
    ax[0].plot(t_frame, means, lw=0.9, color="#7fb3ff")
    hits = np.where(flash)[0]
    if len(hits):
        ax[0].vlines(t_pair[hits], 0, 1, color=warn, lw=0.8, alpha=0.7,
                     label=f"flash ({len(hits)})")
        ax[0].legend(facecolor=bg, edgecolor="#3a3a44", labelcolor=fg,
                     fontsize=9)
    ax[0].set_ylim(0, 1)
    ax[0].set_ylabel("mean luminance", color=fg, fontsize=10)
    ax[0].set_title("Luminance over time", color=fg, fontsize=12,
                    loc="left", pad=10)

    # --- 2: flashes per one-second window ---
    ax[1].fill_between(t_pair, counts, color=accent, alpha=0.35)
    ax[1].plot(t_pair, counts, lw=1.0, color=accent)
    ax[1].axhline(FLASH_PER_SEC, color=warn, lw=1.2, ls="--",
                  label=f"risk threshold ({FLASH_PER_SEC}/s)")
    ax[1].legend(facecolor=bg, edgecolor="#3a3a44", labelcolor=fg,
                 fontsize=9)
    ax[1].set_ylabel("flashes / second", color=fg, fontsize=10)
    ax[1].set_xlabel("time (s)", color=fg, fontsize=10)
    ax[1].set_ylim(0, max(FLASH_PER_SEC + 1.5, counts.max() * 1.25 + 0.5))
    ax[1].set_title("Flash rate", color=fg, fontsize=12, loc="left",
                    pad=10)

    # --- 3: pattern strength as a gauge ---
    ax[2].barh([0], [score], height=0.45,
               color=warn if score > PATTERN_HIGH else
               (accent if score > PATTERN_MODERATE else "#6ad19a"))
    ax[2].axvline(PATTERN_MODERATE, color="#9a9aa6", lw=1.0, ls=":")
    ax[2].axvline(PATTERN_HIGH, color=warn, lw=1.2, ls="--")
    ax[2].text(PATTERN_MODERATE, -0.33, "moderate", color="#9a9aa6",
               fontsize=8.5, ha="center", va="top")
    ax[2].text(PATTERN_HIGH, -0.33, "high", color=warn,
               fontsize=8.5, ha="center", va="top")
    ax[2].text(score, 0, f"  {score:.3f}", color=fg, fontsize=11,
               va="center", fontweight="bold")
    ax[2].set_xlim(0, max(0.75, score * 1.3))
    ax[2].set_yticks([])
    ax[2].set_ylim(-0.55, 0.4)
    ax[2].set_xlabel("spectral energy in strongest peaks", color=fg,
                     fontsize=10)
    ax[2].set_title("Regular-pattern strength", color=fg, fontsize=12,
                    loc="left", pad=10)

    fig.suptitle(f"flashcheck  -  {path}", color=fg, fontsize=13,
                 x=0.02, ha="left", y=0.985)
    fig.text(0.02, 0.005,
             "Screening heuristic, not a certified assessment. "
             "No red-flash test, no luminance calibration.",
             color="#7a7a86", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.02, 1, 0.965])
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    fig.savefig(out_png, dpi=140, facecolor=bg)
    print(f"\n[plot] written to {out_png}")


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Screen a video for photosensitivity risk factors.")
    ap.add_argument("video")
    ap.add_argument("--width", type=int, default=320,
                    help="analysis width in pixels (default 320)")
    ap.add_argument("--json", metavar="FILE",
                    help="also write the report as JSON")
    ap.add_argument("--plot", metavar="FILE",
                    help="write a visual report as PNG (needs matplotlib)")
    args = ap.parse_args()

    frames, fps = load_frames(args.video, args.width)
    duration = len(frames) / fps
    print(f"[video] {len(frames)} frames, {fps:.1f} fps, {duration:.1f} s")

    flash = find_flashes(frames)
    counts = busiest_window(flash, fps)
    worst = float(counts.max()) if len(counts) else 0.0
    total = int(flash.sum())

    print(f"\n[luminance] total flashes: {total}")
    print(f"[luminance] busiest one-second window: {worst:.0f}")
    if worst > FLASH_PER_SEC:
        flash_verdict = "high"
        print(f"  >>> RISK: more than {FLASH_PER_SEC} large-area flashes "
              f"within one second")
    elif total > 0:
        flash_verdict = "low"
        print("  >>> low risk: flashes present but below threshold")
    else:
        flash_verdict = "none"
        print("  >>> no large-area luminance flashes detected")

    if total:
        order = np.argsort(counts)[-5:]
        times = sorted({round(i / fps, 1) for i in order})
        print("  busiest moments (s): " +
              ", ".join(f"{t:.1f}" for t in times))

    score = pattern_strength(frames)
    print(f"\n[pattern] regular-pattern strength: {score:.3f}")
    if score > PATTERN_HIGH:
        pattern_verdict = "high"
        print("  >>> HIGH: strong periodic structure "
              "(stripe/pattern sensitivity risk)")
    elif score > PATTERN_MODERATE:
        pattern_verdict = "moderate"
        print("  >>> MODERATE: noticeable periodic structure")
    else:
        pattern_verdict = "low"
        print("  >>> low")

    print("\n[advice]")
    if flash_verdict == "high" or pattern_verdict == "high":
        print("  Add a photosensitivity warning to this video,")
        print("  in the description and in the opening seconds.")
    else:
        print("  Thresholds not exceeded. A short warning is still")
        print("  cheap insurance and costs the viewer nothing.")
    print("\n  Note: this is a screening heuristic, not a certified")
    print("  assessment. See README for limitations.")

    if args.plot:
        write_plot(args.video, frames, fps, flash, counts, score, args.plot)

    if args.json:
        report = {
            "file": args.video,
            "frames": int(len(frames)),
            "fps": fps,
            "duration_s": duration,
            "flash_total": total,
            "flash_busiest_second": worst,
            "flash_verdict": flash_verdict,
            "pattern_strength": score,
            "pattern_verdict": pattern_verdict,
            "thresholds": {
                "flash_delta": FLASH_DELTA,
                "flash_area": FLASH_AREA,
                "flash_per_second": FLASH_PER_SEC,
                "pattern_high": PATTERN_HIGH,
            },
        }
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\n[json] written to {args.json}")


if __name__ == "__main__":
    main()
