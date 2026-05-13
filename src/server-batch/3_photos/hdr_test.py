"""HDR exposure-fusion test harness.

For each picked bracket set:
- Decodes every member NEF via rawpy (camera WB, no auto-bright)
- Runs cv2.createMergeMertens() exposure fusion
- Writes: each member as a downsized JPEG + the HDR composite + a 4-up grid
- Reports: mean luminance, clipped pixel % (both ends), and histogram entropy
  for each member and the fused result

Usage: python hdr_test.py
Outputs: f:/_repos/ArtemisInRealTime/hdr_test_out/{set_id}/
"""

import json
import math
from pathlib import Path

import cv2
import numpy as np
import rawpy
from PIL import Image

PICK_FILE = Path("f:/_repos/ArtemisInRealTime/hdr_test_picks.json")
RAW_ROOT = Path("F:/_repos/ArtemisInRealTime_assets/artemis-ii/raw/photos/5_Crew-Captured-Imagery")
OUT_ROOT = Path("f:/_repos/ArtemisInRealTime/hdr_test_out")
LONG_EDGE = 1280  # downscale before fusion — fusion is O(n) so faster, still good enough to judge

# ── Helpers ─────────────────────────────────────────────────────────────────


def find_nef(nasa_id: str) -> Path | None:
    """Recursive lookup — raw_crew has FD_NN/D5_15 subfolders."""
    for p in RAW_ROOT.rglob(f"{nasa_id}.NEF"):
        return p
    for p in RAW_ROOT.rglob(f"{nasa_id}.DNG"):
        return p
    return None


def decode_nef(path: Path, no_auto_bright: bool = True) -> np.ndarray:
    """Decode NEF → uint8 RGB.

    `no_auto_bright=True`  → preserves relative exposure between bracket
        members (required for HDR fusion).
    `no_auto_bright=False` → auto-stretches a single image's histogram. What
        we'd publish for a single tier-generated frame.
    """
    with rawpy.imread(str(path)) as raw:
        return raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=no_auto_bright,
            output_bps=8,
        )


def resize_long(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) <= long_edge:
        return img
    if w >= h:
        new_w, new_h = long_edge, round(h * long_edge / w)
    else:
        new_h, new_w = long_edge, round(w * long_edge / h)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def metrics(img: np.ndarray) -> dict:
    """Mean luminance, %clipped low, %clipped high, entropy of luma histogram."""
    if img.ndim == 3:
        # cv2 uses BGR; rec.601 luma weights
        b, g, r = cv2.split(img)
        luma = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)
    else:
        luma = img
    total = luma.size
    clip_low = float((luma == 0).sum()) / total * 100
    clip_high = float((luma == 255).sum()) / total * 100
    hist = cv2.calcHist([luma], [0], None, [256], [0, 256]).flatten()
    p = hist / hist.sum()
    entropy = -float(sum(pi * math.log2(pi) for pi in p if pi > 0))
    return {
        "mean": float(luma.mean()),
        "clip_low_pct": clip_low,
        "clip_high_pct": clip_high,
        "entropy": entropy,
    }


def grid_4up(images: list[np.ndarray], labels: list[str]) -> np.ndarray:
    """Lay out 4 same-size BGR images in a 2x2 grid with labels."""
    h, w = images[0].shape[:2]
    pad = 8
    label_h = 28
    cell = np.zeros((h + label_h, w, 3), dtype=np.uint8)
    cells = []
    for img, label in zip(images, labels):
        c = cell.copy()
        c[label_h:, :] = img
        cv2.putText(c, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cells.append(c)
    top = np.hstack([cells[0], np.zeros((cells[0].shape[0], pad, 3), dtype=np.uint8), cells[1]])
    bot = np.hstack([cells[2], np.zeros((cells[2].shape[0], pad, 3), dtype=np.uint8), cells[3]])
    return np.vstack([top, np.zeros((pad, top.shape[1], 3), dtype=np.uint8), bot])


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    with open(PICK_FILE, "r", encoding="utf-8") as f:
        picks = json.load(f)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    fuser = cv2.createMergeMertens()

    print(f"\n{'='*100}")
    print(f"  HDR exposure-fusion test ({len(picks)} sets)")
    print(f"{'='*100}\n")

    for pick in picks:
        set_id = pick["set_id"]
        members = pick["members"]
        evs = pick["evs"]
        out_dir = OUT_ROOT / set_id
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"--- {set_id}  LV={pick['lv']:.1f}  ISO={pick['iso']}  evs={evs} ---")

        # Decode each member twice:
        #   bgr_fuse    → no_auto_bright (linear-ish, exposure-preserving) for HDR
        #   bgr_display → auto-bright (single-frame "what we'd publish") for the grid
        bgr_fuse: list = []
        bgr_display: list = []
        skipped = False
        for nid in members:
            nef = find_nef(nid)
            if nef is None:
                print(f"  MISSING NEF for {nid} — skipping set")
                skipped = True
                break
            try:
                rgb_f = decode_nef(nef, no_auto_bright=True)
                rgb_d = decode_nef(nef, no_auto_bright=False)
            except Exception as e:
                print(f"  DECODE FAIL on {nid}: {e}")
                skipped = True
                break
            rgb_f = resize_long(rgb_f, LONG_EDGE)
            rgb_d = resize_long(rgb_d, LONG_EDGE)
            bgr_fuse.append(cv2.cvtColor(rgb_f, cv2.COLOR_RGB2BGR))
            bgr_display.append(cv2.cvtColor(rgb_d, cv2.COLOR_RGB2BGR))
        if skipped:
            continue

        # Use the display versions for individual frame outputs and grid
        bgr_imgs = bgr_display

        # Align the no-auto-bright frames to compensate for camera motion
        # between bracket exposures. AlignMTB is parameter-free and fast.
        try:
            aligner = cv2.createAlignMTB()
            aligned = []
            for im in bgr_fuse:
                aligned.append(im.copy())
            aligner.process(bgr_fuse, aligned)
            bgr_fuse = aligned
        except Exception as e:
            print(f"  alignment failed ({e}), using unaligned frames")

        # Save individual member JPEGs (sorted by EV ascending so darker→lighter)
        order = sorted(range(len(evs)), key=lambda i: evs[i])
        for rank, idx in enumerate(order):
            cv2.imwrite(str(out_dir / f"{rank}_ev{evs[idx]:+.1f}_{members[idx]}.jpg"),
                        bgr_imgs[idx], [cv2.IMWRITE_JPEG_QUALITY, 88])

        # Fuse — Mertens expects float32 in [0..1]. Output range is normally
        # ≪1 (opencv quirk) so we always normalize. Use a 5th-percentile
        # black point (crushes shadow noise) and 99.7th-percentile white
        # point (ignores specular highlights / dead pixels).
        # IMPORTANT: feed the no-auto-bright versions so exposure variation
        # is preserved; auto-bright would equalize the brackets and defeat
        # the point of fusion.
        fused = fuser.process([im.astype(np.float32) / 255.0 for im in bgr_fuse])
        lo = float(np.percentile(fused, 5))
        hi = float(np.percentile(fused, 99.7))
        if hi > lo + 1e-6:
            fused_n = np.clip((fused - lo) / (hi - lo), 0, 1)
        else:
            fused_n = np.clip(fused, 0, 1)
        fused_8u = (fused_n * 255.0).astype(np.uint8)

        # Light denoise — non-local means is slow but high quality. Use a
        # small h to preserve crater / cloud detail.
        fused_dn = cv2.fastNlMeansDenoisingColored(fused_8u, None,
                                                   h=4, hColor=4,
                                                   templateWindowSize=7,
                                                   searchWindowSize=21)
        cv2.imwrite(str(out_dir / f"hdr_{set_id}.jpg"), fused_dn,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        # Use the denoised version in the comparison grid + metrics
        fused_8u = fused_dn

        # 4-up comparison grid
        # Need exactly 4 — if 3 members, pad with HDR result
        comp_imgs = [bgr_imgs[i] for i in order] + [fused_8u]
        comp_labels = [f"EV{evs[i]:+.1f}" for i in order] + ["HDR fused"]
        if len(comp_imgs) > 4:
            comp_imgs = comp_imgs[:4]
            comp_labels = comp_labels[:4]
        elif len(comp_imgs) < 4:
            blank = np.zeros_like(bgr_imgs[0])
            while len(comp_imgs) < 4:
                comp_imgs.append(blank)
                comp_labels.append("")
        grid = grid_4up(comp_imgs, comp_labels)
        cv2.imwrite(str(out_dir / f"COMPARE_{set_id}.jpg"), grid,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])

        # Metrics
        print(f"  {'frame':<24s}  {'mean':>6}  {'clip_low%':>9}  {'clip_high%':>10}  {'entropy':>7}")
        for idx in order:
            m = metrics(bgr_imgs[idx])
            print(f"  EV{evs[idx]:+.1f} {members[idx]:<14s}  "
                  f"{m['mean']:>6.1f}  {m['clip_low_pct']:>8.2f}%  "
                  f"{m['clip_high_pct']:>9.2f}%  {m['entropy']:>7.3f}")
        m = metrics(fused_8u)
        print(f"  {'HDR fused':<24s}  {m['mean']:>6.1f}  {m['clip_low_pct']:>8.2f}%  "
              f"{m['clip_high_pct']:>9.2f}%  {m['entropy']:>7.3f}")
        print()

    print(f"Outputs saved to {OUT_ROOT}\n")


if __name__ == "__main__":
    main()
