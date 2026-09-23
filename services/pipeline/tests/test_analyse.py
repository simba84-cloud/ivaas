import cv2
import numpy as np

from ivaas_pipeline.analyse import analyse_video
from ivaas_pipeline.stages.layers import LayerEstimate
from ivaas_pipeline.types import Box, Detection


class ScriptedDetector:
    """A stack that rolls in over 3 s, settles for 5 s, then a second one 3 min later."""

    def __init__(self, fps):
        self.fps, self.i = fps, -1

    def detect(self, frame):
        self.i += 1
        t = self.i * 2 / self.fps  # stride 2
        for start in (2.0, 200.0):
            if start <= t < start + 3:
                x = 900 - (t - start) * 250
                return [Detection(Box(x, 100, x + 120, 700), "stack", 0.9)]
            if start + 3 <= t < start + 8:
                return [Detection(Box(150, 100, 270, 700), "stack", 0.9)]
        return []


class Layers:
    def estimate(self, image, box):
        return LayerEstimate(12.0, 50, 0.5)


def test_analyse_finds_loads_and_captures_frames(tmp_path):
    fps = 10
    path = str(tmp_path / "v.mp4")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    for _ in range(fps * 215):
        w.write(np.zeros((180, 320, 3), np.uint8))
    w.release()
    saved = []
    res = analyse_video(
        path,
        detector=ScriptedDetector(fps),
        layers=Layers(),
        save_frame=lambda name, img: saved.append(name) or name,
        stride=2,
        idle_seconds=120,
        progress=lambda p: None,
    )
    assert abs(res.duration_s - 215) < 1
    assert [(ld.stacks, ld.crates) for ld in res.loads] == [(1, 12), (1, 12)]
    assert len(saved) == 2
    kinds = [e.kind for e in res.timeline]
    assert (
        kinds.count("stack_counted") == 2
        and kinds.count("load_started") == 2
        and kinds.count("load_ended") == 2
    )
    assert res.timeline == sorted(res.timeline, key=lambda e: e.at_s)
