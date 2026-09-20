#!/usr/bin/env python3
"""
Generate synthetic validation clips with known properties.

The thresholds in flashcheck.py are only meaningful if the tool gives
the right answer on content whose risk profile is known by construction.
These clips are that reference set.

    python make_validation.py        # writes to validation/
    for f in validation/*.mp4; do python flashcheck.py "$f"; done
"""
import subprocess
import os
import numpy as np

W, H, FPS, N = 640, 360, 30, 300           # 10 seconds
OUT = "validation"


def write(name, gen):
    os.makedirs(OUT, exist_ok=True)
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo",
         "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "14",
         f"{OUT}/{name}.mp4"],
        stdin=subprocess.PIPE)
    for i in range(N):
        p.stdin.write(gen(i).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()
    print(f"  {name}.mp4")


yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

CLIPS = {
    # slow luminance drift: no flashes, no pattern
    "01_safe_gradient":
        lambda i: np.clip((0.35 + 0.25 * np.sin(2 * np.pi * (i / N) * 0.5))
                          * 255 * np.ones((H, W)), 0, 255),
    # full-screen 10 Hz strobe: unambiguously above the flash threshold
    "02_strobe_10hz":
        lambda i: np.full((H, W), 255 if (i // (FPS // 20)) % 2 == 0 else 0,
                          np.float32),
    # 2 Hz alternation: right at the published threshold, should not alarm
    "03_flash_2hz":
        lambda i: np.full((H, W), 255 if (i // (FPS // 4)) % 2 == 0 else 0,
                          np.float32),
    # high-contrast drifting grating: pattern risk, no flashes
    "04_moving_grating":
        lambda i: (0.5 + 0.5 * np.sign(
            np.sin(2 * np.pi * xx / 14 + 2 * np.pi * i / FPS * 0.5))) * 255,
    # stationary grating: pattern risk without motion
    "05_static_grating":
        lambda i: (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * xx / 14))) * 255,
}

if __name__ == "__main__":
    print("writing validation clips:")
    for name, gen in CLIPS.items():
        write(name, gen)
    print(f"\nnow run:  for f in {OUT}/*.mp4; do "
          f"python flashcheck.py \"$f\"; done")
