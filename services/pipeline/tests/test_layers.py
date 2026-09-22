import cv2
import numpy as np
import pytest

from ivaas_pipeline.stages.layers import PeriodicityLayerCounter
from ivaas_pipeline.types import Box


def draw_stack(layers, pitch=60, width=300, *, noise=0.0, tilt_px=0, seed=0, colour=(200, 60, 40)):
    """A synthetic stack: each crate is a coloured band with a dark rim and handle slots."""
    rng = np.random.default_rng(seed)
    height = layers * pitch
    canvas = np.full((height + 200, width + 200 + abs(tilt_px), 3), 35, np.uint8)
    for k in range(layers):
        y = 100 + k * pitch
        x = 100 + int(tilt_px * k / max(1, layers - 1))
        cv2.rectangle(canvas, (x, y), (x + width, y + pitch - 6), colour, -1)
        cv2.rectangle(canvas, (x, y + pitch - 6), (x + width, y + pitch), (20, 20, 20), -1)
        for slot in range(3):
            sx = x + int(width * (0.1 + 0.3 * slot))
            cv2.rectangle(
                canvas, (sx, y + pitch // 4), (sx + width // 6, y + pitch // 2), (25, 25, 25), -1
            )
    if noise:
        canvas = np.clip(canvas + rng.normal(0, noise, canvas.shape), 0, 255).astype(np.uint8)
    box = Box(100, 100, 100 + width + abs(tilt_px), 100 + height)
    return canvas, box


@pytest.mark.parametrize("layers", [3, 8, 12, 15, 20])
def test_counts_clean_stacks_exactly(layers):
    image, box = draw_stack(layers)
    est = PeriodicityLayerCounter().estimate(image, box)
    assert round(est.layers) == layers
    assert est.confidence > 0.5


@pytest.mark.parametrize("pitch", [24, 45, 82])
def test_pitch_independent_of_camera_distance(pitch):
    image, box = draw_stack(14, pitch=pitch, width=pitch * 5)  # a crate keeps its shape
    est = PeriodicityLayerCounter().estimate(image, box)
    assert round(est.layers) == 14 and abs(est.pitch_px - pitch) <= 1


def test_survives_sensor_noise_and_blur():
    image, box = draw_stack(13, noise=25, seed=3)
    image = cv2.GaussianBlur(image, (7, 7), 0)
    assert round(PeriodicityLayerCounter().estimate(image, box).layers) == 13


def test_survives_a_tilted_dragged_stack():
    image, box = draw_stack(14, tilt_px=120)
    assert round(PeriodicityLayerCounter().estimate(image, box).layers) == 14


def test_does_not_lock_onto_a_harmonic():
    # the classic failure: reporting 7 (2x pitch) for a 14-crate stack
    image, box = draw_stack(14, pitch=40, noise=10)
    assert round(PeriodicityLayerCounter().estimate(image, box).layers) == 14


def test_refuses_featureless_regions_instead_of_guessing():
    wall = np.full((800, 600, 3), 128, np.uint8)
    assert PeriodicityLayerCounter().estimate(wall, Box(100, 100, 400, 700)) is None
    rng = np.random.default_rng(1)
    static = rng.integers(0, 255, (800, 600, 3), dtype=np.uint8)
    assert PeriodicityLayerCounter().estimate(static, Box(100, 100, 400, 700)) is None


def test_fine_texture_inside_crates_does_not_beat_the_layer_pitch():
    # real crates have a lattice at ~1/3 of the layer pitch; without the width prior the
    # counter locked onto it and reported 25 layers for an 8-crate stack
    image, box = draw_stack(8, pitch=75, width=400)
    for y in range(100, 100 + 8 * 75, 25):
        cv2.line(image, (100, y), (500, y), (15, 15, 15), 3)
    assert round(PeriodicityLayerCounter().estimate(image, box).layers) == 8


def test_tiny_or_out_of_frame_boxes_are_rejected():
    image, _ = draw_stack(5)
    counter = PeriodicityLayerCounter()
    assert counter.estimate(image, Box(10, 10, 14, 14)) is None
    assert counter.estimate(image, Box(-500, -500, -100, -100)) is None
