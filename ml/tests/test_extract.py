from pathlib import Path

import cv2
import numpy as np

from ivaas_ml.extract_frames import camera_of, change_score, extract_clip, signature, thin_evenly


def test_camera_name_from_nvr_export():
    assert camera_of(Path("CC 2_17_S20260619092441_E20260619103235.mp4")) == "cc2"
    assert (
        camera_of(Path("CRATES COUNTING_125_S20260618035723_E20260618035724.mp4"))
        == "cratescounting"
    )
    assert camera_of(Path("random clip.mp4")) == "randomclip"


def test_thin_evenly_keeps_both_ends():
    assert thin_evenly(list(range(100)), 5) == [0, 25, 50, 74, 99]
    assert thin_evenly([1, 2], 5) == [1, 2]


def test_change_score_ignores_noise_but_sees_objects():
    rng = np.random.default_rng(0)
    base = np.full((540, 960, 3), 120, np.uint8)
    noisy = np.clip(base.astype(int) + rng.integers(-6, 6, base.shape), 0, 255).astype(np.uint8)
    moved = base.copy()
    cv2.rectangle(moved, (100, 100), (500, 450), (255, 40, 40), -1)
    assert change_score(signature(base), signature(noisy)) < 0.01
    assert change_score(signature(base), signature(moved)) > 0.1


def test_extract_covers_whole_clip_and_skips_static(tmp_path):
    clip = tmp_path / "CAM 1_1_S20260101000000_E20260101000100.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for i in range(600):  # 60s: a block appears only in the final third
        img = np.full((180, 320, 3), 90, np.uint8)
        if i >= 400:
            cv2.rectangle(
                img, (20 + (i - 400) // 2, 40), (120 + (i - 400) // 2, 140), (255, 0, 0), -1
            )
        writer.write(img)
    writer.release()

    out = tmp_path / "frames"
    out.mkdir()
    kept = extract_clip(clip, out, every_s=1, min_change=0.04, max_frames=6)
    assert 2 <= len(kept) <= 6
    assert kept[0].frame_index == 0
    assert kept[-1].seconds > 40  # reached the activity late in the clip
    assert sum(1 for k in kept if k.seconds < 39) == 1  # the static two-thirds yields one frame
    assert all((out / k.file).exists() for k in kept)
