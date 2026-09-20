#!/usr/bin/env python3
"""
Run the validation suite and print a pass/fail table.

Thresholds are only meaningful if the tool gives the right answer on
content whose risk profile is known by construction. This script builds
that reference set and checks the tool against it.

    python validate.py
    python validate.py --plot examples/validation.png

Clips are generated on first run and reused afterwards. Pass --rebuild
to regenerate them.
"""

import argparse
import os
import sys

import make_validation as mv
import flashcheck as fc


# name -> (expected flash verdict, expected pattern verdict)
EXPECTED = {
    "01_safe_gradient":   ("none", "low"),
    "02_strobe_10hz":     ("high", "low"),
    "03_flash_2hz":       ("low",  "low"),
    "04_moving_grating":  ("none", "high"),
    "05_static_grating":  ("none", "high"),
}

WHY = {
    "01_safe_gradient":  "slow drift, no structure",
    "02_strobe_10hz":    "far above the flash threshold",
    "03_flash_2hz":      "at the published threshold, must not alarm",
    "04_moving_grating": "drifting stripes, no luminance flashes",
    "05_static_grating": "stationary stripes",
}



def write_summary(rows, out_png):
    """Figure showing both measurements across the reference set."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[plot] matplotlib not installed - skipping")
        return

    bg, fgc = "#101014", "#e8e8ec"
    warn, ok_c, mid = "#ff5c5c", "#6ad19a", "#ffb86b"
    labels = [r[0].split("_", 1)[1].replace("_", " ") for r in rows]
    flashes = [r[1] for r in rows]
    patterns = [r[3] for r in rows]
    passed = [(r[2] == r[5]) and (r[4] == r[6]) for r in rows]

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6), facecolor=bg)
    for a in ax:
        a.set_facecolor(bg)
        for sp in a.spines.values():
            sp.set_color("#3a3a44")
        a.tick_params(colors="#9a9aa6", labelsize=9)
        a.grid(alpha=0.12, color=fgc, axis="x")

    y = range(len(rows))
    ax[0].barh(list(y), flashes,
               color=[warn if f > fc.FLASH_PER_SEC else ok_c
                      for f in flashes], height=0.55)
    ax[0].axvline(fc.FLASH_PER_SEC, color=warn, ls="--", lw=1.2,
                  label=f"threshold ({fc.FLASH_PER_SEC}/s)")
    ax[0].set_yticks(list(y)); ax[0].set_yticklabels(labels, color=fgc)
    ax[0].invert_yaxis()
    ax[0].set_xlabel("flashes per second", color=fgc, fontsize=10)
    ax[0].set_title("Flash rate", color=fgc, fontsize=12, loc="left")
    ax[0].legend(facecolor=bg, edgecolor="#3a3a44", labelcolor=fgc,
                 fontsize=9)

    ax[1].barh(list(y), patterns,
               color=[warn if p > fc.PATTERN_HIGH else
                      (mid if p > fc.PATTERN_MODERATE else ok_c)
                      for p in patterns], height=0.55)
    ax[1].axvline(fc.PATTERN_HIGH, color=warn, ls="--", lw=1.2,
                  label=f"high ({fc.PATTERN_HIGH})")
    ax[1].set_yticks(list(y)); ax[1].set_yticklabels([])
    ax[1].invert_yaxis()
    ax[1].set_xlim(0, 1.05)
    ax[1].set_xlabel("regular-pattern strength", color=fgc, fontsize=10)
    ax[1].set_title("Pattern strength", color=fgc, fontsize=12, loc="left")
    ax[1].legend(facecolor=bg, edgecolor="#3a3a44", labelcolor=fgc,
                 fontsize=9)

    exp_txt = {("none", "low"): "expect: clean",
               ("high", "low"): "expect: FLASH risk",
               ("low", "low"): "expect: below threshold",
               ("none", "high"): "expect: PATTERN risk"}
    for i, (r, p) in enumerate(zip(rows, passed)):
        ax[1].text(1.10, i, exp_txt.get((r[5], r[6]), ""),
                   color="#9a9aa6", fontsize=8.5, va="center", ha="left")
        ax[1].text(1.10, i - 0.30, "tool agrees" if p else "TOOL WRONG",
                   color=ok_c if p else warn, fontsize=8.5,
                   va="center", ha="left", fontweight="bold")

    fig.suptitle("flashcheck validation - clips with known risk profiles",
                 color=fgc, fontsize=13, x=0.012, ha="left", y=0.985)
    fig.text(0.012, 0.915,
             "Green/red bars show what the tool measured. The right-hand "
             "note is what the clip is by construction, and whether the "
             "tool agreed.",
             color="#9a9aa6", fontsize=9)
    fig.tight_layout(rect=[0, 0, 0.87, 0.90])
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    fig.savefig(out_png, dpi=140, facecolor=bg)
    print(f"[plot] written to {out_png}")


def verdicts(path):
    frames, fps = fc.load_frames(path)
    flash = fc.find_flashes(frames)
    counts = fc.busiest_window(flash, fps)
    worst = float(counts.max()) if len(counts) else 0.0
    total = int(flash.sum())
    score = fc.pattern_strength(frames)

    if worst > fc.FLASH_PER_SEC:
        fv = "high"
    elif total > 0:
        fv = "low"
    else:
        fv = "none"

    if score > fc.PATTERN_HIGH:
        pv = "high"
    elif score > fc.PATTERN_MODERATE:
        pv = "moderate"
    else:
        pv = "low"
    return worst, fv, score, pv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate the clips even if they exist")
    ap.add_argument("--plot", metavar="FILE",
                    help="write a summary figure as PNG (needs matplotlib)")
    args = ap.parse_args()

    need = args.rebuild or not all(
        os.path.exists(os.path.join(mv.OUT, f"{n}.mp4")) for n in EXPECTED)
    if need:
        print("generating validation clips:")
        for name, gen in mv.CLIPS.items():
            mv.write(name, gen)
        print()

    head = (f"{'clip':<20}{'flash/s':>8}{'verdict':>9}"
            f"{'pattern':>9}{'verdict':>10}   {'result':<6} why")
    print(head)
    print("-" * len(head))

    failures = 0
    rows = []
    for name, (exp_f, exp_p) in EXPECTED.items():
        path = os.path.join(mv.OUT, f"{name}.mp4")
        worst, fv, score, pv = verdicts(path)
        rows.append((name, worst, fv, score, pv, exp_f, exp_p))
        ok = (fv == exp_f) and (pv == exp_p)
        if not ok:
            failures += 1
        print(f"{name:<20}{worst:>8.0f}{fv:>9}{score:>9.3f}{pv:>10}   "
              f"{'pass' if ok else 'FAIL':<6} {WHY[name]}")
        if not ok:
            print(f"{'':<20}expected flash={exp_f}, pattern={exp_p}")

    print()
    if args.plot:
        write_summary(rows, args.plot)
    if failures:
        print(f"{failures} of {len(EXPECTED)} checks FAILED")
        sys.exit(1)
    print(f"all {len(EXPECTED)} checks passed")


if __name__ == "__main__":
    main()
