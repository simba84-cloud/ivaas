"""Golden-footage regression: the real models on a real clip, against a frozen result.

Anything that changes what the pipeline counts (a retrained detector, a tracker
tweak, a settle threshold) shows up here as a diff against `door-load.expected.json`.
When a change is *intended*, regenerate the expectation and commit both:

    IVAAS_GOLDEN_UPDATE=1 uv run pytest tests/test_golden.py

Needs the ONNX models (models/ at the repo root); skipped when they are absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CLIP = HERE / "golden" / "door-load.mp4"
EXPECTED = HERE / "golden" / "door-load.expected.json"
MODELS = HERE.parents[2] / "models"
STACKS = MODELS / "stacks-v2.onnx"
LAYERS = MODELS / "layers-v3.onnx"


@pytest.mark.golden
@pytest.mark.skipif(not (STACKS.exists() and LAYERS.exists()), reason="ONNX models not present")
def test_door_camera_load_matches_the_frozen_result():
    from ivaas_pipeline.adapters.onnx_layers import OnnxLayerCounter
    from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector
    from ivaas_pipeline.analyse import analyse_video

    result = analyse_video(
        str(CLIP),
        detector=OnnxRtDetrDetector(str(STACKS), threshold=0.2),
        layers=OnnxLayerCounter(str(LAYERS)),
        plate_reader=None,
        save_frame=None,
        stride=1,
    )
    got = {
        "duration_s": round(result.duration_s, 1),
        "loads": [
            {
                "start_s": round(ld.start_s, 1),
                "end_s": round(ld.end_s, 1),
                "stacks": ld.stacks,
                "crates": ld.crates,
            }
            for ld in result.loads
        ],
        "stacks": [
            {"at_s": round(e.at_s, 1), "crates": int(e.detail.split()[2])}
            for e in result.timeline
            if e.kind == "stack_counted"
        ],
    }
    if os.environ.get("IVAAS_GOLDEN_UPDATE"):
        EXPECTED.write_text(json.dumps(got, indent=1) + "\n")
        pytest.skip(f"expectation regenerated -> {EXPECTED.name}")
    assert EXPECTED.exists(), "no expectation yet: run with IVAAS_GOLDEN_UPDATE=1"
    expected = json.loads(EXPECTED.read_text())
    assert got == expected, (
        "pipeline output changed. If intended, regenerate with IVAAS_GOLDEN_UPDATE=1.\n"
        f"expected: {json.dumps(expected)}\ngot:      {json.dumps(got)}"
    )
