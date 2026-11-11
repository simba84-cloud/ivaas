from datetime import UTC, datetime

import httpx
import numpy as np
import pytest

from ivaas_pipeline.adapters.http_detector import DetectorUnavailable, HttpDetector, parse
from ivaas_pipeline.types import Box, Frame


def frame(w=640, h=480):
    return Frame("cam", np.zeros((h, w, 3), np.uint8), datetime(2026, 11, 11, tzinfo=UTC))


def detector(handler, **kw):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer k"}
    )
    kw.setdefault("sleep", lambda s: None)
    return HttpDetector("http://gpu/detect", "k", client=client, **kw)


def hf_answer(*rows):
    return [
        {"score": s, "label": lab, "box": {"xmin": a, "ymin": b, "xmax": c, "ymax": d}}
        for lab, s, (a, b, c, d) in rows
    ]


def test_sends_jpeg_threshold_and_key_and_reads_the_pipeline_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        body = request.read()
        seen["jpeg"] = b"image/jpeg" in body and b'name="image"' in body
        seen["threshold"] = b'name="threshold"' in body and b"0.35" in body
        return httpx.Response(200, json=hf_answer(("stack", 0.9, (10, 20, 110, 220))))

    dets = detector(handler, threshold=0.35).detect(frame())
    assert seen == {"auth": "Bearer k", "jpeg": True, "threshold": True}
    assert len(dets) == 1
    assert dets[0].label == "stack" and dets[0].confidence == 0.9
    assert dets[0].box == Box(10, 20, 110, 220)


def test_reads_the_arrays_shape():
    payload = {"boxes": [[1, 2, 3, 4]], "labels": ["person"], "scores": [0.8]}
    (d,) = parse(payload, scale=1.0, width=100, height=100)
    assert d.label == "person" and d.box == Box(1, 2, 3, 4)


def test_a_large_frame_is_shrunk_and_boxes_come_back_in_original_pixels():
    sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(request.read()))
        # the service saw a 1280x720 image; a box on its right half
        return httpx.Response(200, json=hf_answer(("stack", 0.7, (640, 0, 1280, 720))))

    (d,) = detector(handler, max_side=1280).detect(frame(3840, 2160))
    assert d.box == Box(1920, 0, 3840, 2160)


def test_boxes_are_clipped_to_the_frame_and_empty_ones_dropped():
    payload = hf_answer(("stack", 0.9, (-5, -5, 700, 500)), ("stack", 0.9, (50, 50, 50, 90)))
    (d,) = parse(payload, scale=1.0, width=640, height=480)
    assert d.box == Box(0, 0, 640, 480)


def test_retries_a_5xx_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[])

    assert detector(handler, retries=3).detect(frame()) == []
    assert len(calls) == 3


def test_an_unreachable_detector_raises_rather_than_reporting_an_empty_frame():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    with pytest.raises(DetectorUnavailable, match="after 3 attempts"):
        detector(handler, retries=2).detect(frame())
    assert len(calls) == 3


def test_a_rejected_key_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(DetectorUnavailable, match="HTTP 401"):
        detector(handler, retries=3).detect(frame())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": True},
        [{"label": "stack"}],
        {"boxes": [[1, 2, 3, 4]], "labels": [], "scores": [0.5]},
    ],
)
def test_a_malformed_answer_raises(payload):
    with pytest.raises(DetectorUnavailable):
        parse(payload, scale=1.0, width=10, height=10)


def test_the_detections_wrapper_is_accepted():
    payload = {"detections": hf_answer(("stack", 0.6, (1, 1, 5, 5)))}
    assert len(parse(payload, scale=1.0, width=10, height=10)) == 1


def test_a_url_is_required():
    with pytest.raises(ValueError):
        HttpDetector("")
