"""Sample training frames from recorded footage.

CCTV is mostly static, so uniform sampling wastes labelling effort on hundreds
of near-identical frames. We step through each clip at a fixed interval and
keep a frame only if it differs enough from the last one kept for that clip.

    uv run python -m ivaas_ml.extract_frames "<footage dir>" data/frames --every 2 --max-per-clip 60
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

OVERSAMPLE = 4  # candidates examined per frame kept, before change-filtering
_CAMERA_RE = re.compile(r"^(?P<camera>.+?)_\d+_S\d{14}")


@dataclass(frozen=True)
class Kept:
    file: str
    camera: str
    clip: str
    frame_index: int
    seconds: float
    change: float


def camera_of(clip: Path) -> str:
    """'CC 2_17_S2026...mp4' -> 'cc2' (NVR export naming: <camera>_<n>_S<start>_E<end>)."""
    m = _CAMERA_RE.match(clip.name)
    name = m.group("camera") if m else clip.stem
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "camera"


def signature(image: np.ndarray) -> np.ndarray:
    small = cv2.resize(image, (96, 54), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.int16)


def change_score(a: np.ndarray, b: np.ndarray) -> float:
    """Fraction of the (downscaled) image whose brightness moved noticeably."""
    return float((np.abs(a - b) > 18).mean())


def extract_clip(
    clip: Path, out_dir: Path, *, every_s: float, min_change: float, max_frames: int
) -> list[Kept]:
    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not cap.isOpened() or fps <= 0 or total <= 0:
        cap.release()
        return []

    camera = camera_of(clip)
    # Spread candidates over the *whole* clip. A fixed step plus a frame cap would fill
    # the cap from the first few minutes of a long clip and never see the rest.
    step = max(round(every_s * fps), total // (max_frames * OVERSAMPLE), 1)
    candidates: list[tuple[Kept, bytes]] = []
    last: np.ndarray | None = None
    for index in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = cap.read()
        if not ok:
            continue
        sig = signature(image)
        change = 1.0 if last is None else change_score(sig, last)
        if change < min_change:
            continue
        last = sig
        name = f"{camera}__{clip.stem.replace(' ', '_')}__{index:07d}.jpg"
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            kept = Kept(name, camera, clip.name, index, round(index / fps, 2), round(change, 4))
            candidates.append((kept, jpeg.tobytes()))
    cap.release()

    chosen = thin_evenly(candidates, max_frames)
    for kept, jpeg in chosen:
        (out_dir / kept.file).write_bytes(jpeg)
    return [kept for kept, _ in chosen]


def thin_evenly[T](items: list[T], limit: int) -> list[T]:
    if len(items) <= limit:
        return items
    picks = np.linspace(0, len(items) - 1, limit).round().astype(int)
    return [items[i] for i in picks]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("footage", type=Path, help="directory searched recursively for .mp4 clips")
    ap.add_argument("out", type=Path)
    ap.add_argument("--every", type=float, default=2.0, help="seconds between candidate frames")
    ap.add_argument("--min-change", type=float, default=0.04, help="0..1, vs last kept frame")
    ap.add_argument("--max-per-clip", type=int, default=60)
    ap.add_argument("--skip", default="Copy", help="ignore clips whose name contains this")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    rows: list[Kept] = []
    for clip in sorted(args.footage.rglob("*.mp4")):
        if (args.skip and args.skip in clip.name) or clip.name in seen:
            continue  # NVR exports are often duplicated across folders
        seen.add(clip.name)
        kept = extract_clip(
            clip,
            args.out,
            every_s=args.every,
            min_change=args.min_change,
            max_frames=args.max_per_clip,
        )
        rows += kept
        print(f"{len(kept):4d}  {clip.name}")

    with open(args.out / "manifest.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(Kept.__dataclass_fields__)
        writer.writerows([tuple(vars(r).values()) for r in rows])

    by_camera: dict[str, int] = {}
    for r in rows:
        by_camera[r.camera] = by_camera.get(r.camera, 0) + 1
    print(
        f"\n{len(rows)} frames -> {args.out}   "
        + "  ".join(f"{k}={v}" for k, v in by_camera.items())
    )


if __name__ == "__main__":
    main()
