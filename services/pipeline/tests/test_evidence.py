"""Evidence clips on the node: one clip per burst of counts, spooled, delivered, bounded."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from ivaas_pipeline.adapters.evidence import (
    EvidenceRecorder,
    EvidenceSink,
    EvidenceUploader,
    stream_path,
)
from ivaas_pipeline.types import CrossDirection, Crossing

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
URI = "rtsp://mediamtx:8554/bay/choke-1"


class Clock:
    def __init__(self):
        self.at = T0

    def __call__(self):
        return self.at


def playback(handler) -> httpx.Client:
    return httpx.Client(base_url="http://mediamtx:9996", transport=httpx.MockTransport(handler))


def recorder(tmp_path, handler=lambda r: httpx.Response(200, content=MP4), clock=None):
    return EvidenceRecorder(playback(handler), tmp_path, clock=clock or Clock())


def test_the_stream_path_is_what_the_media_server_calls_it():
    assert stream_path(URI) == "bay/choke-1"


def test_counts_close_together_share_one_clip_and_wait_until_recorded(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock=clock)
    for s in (0, 2, 4):  # a burst of three crossings
        rec.request("choke-1", "cam", URI, "crossing", T0 + timedelta(seconds=s))
    assert rec.due() == []  # the last one's after-window is not on disk yet
    clock.at = T0 + timedelta(seconds=4 + 5 + 4)
    [w] = rec.due()
    assert (w.start, w.end) == (T0 - timedelta(seconds=3), T0 + timedelta(seconds=9))


def test_counts_far_apart_get_their_own_clips(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock=clock)
    rec.request("choke-1", "cam", URI, "crossing", T0)
    rec.request("choke-1", "cam", URI, "crossing", T0 + timedelta(seconds=60))
    clock.at = T0 + timedelta(minutes=5)
    assert len(rec.due()) == 2


def test_a_clip_is_cut_from_the_recording_with_its_metadata(tmp_path):
    asked = {}

    def handler(request):
        asked.update(dict(request.url.params))
        return httpx.Response(200, content=MP4)

    rec = recorder(tmp_path, handler)
    rec.request("choke-1", "cam-uuid", URI, "crossing", T0)
    rec._now = lambda: T0 + timedelta(minutes=1)
    [w] = rec.due()
    video = rec.cut(w)
    assert video.read_bytes() == MP4
    assert asked == {
        "path": "bay/choke-1",
        "start": w.start.isoformat(),
        "duration": "8.0",
        "format": "mp4",
    }
    meta = json.loads(video.with_suffix(".json").read_text())
    assert meta["api_camera_id"] == "cam-uuid" and meta["kind"] == "crossing" and meta["event_id"]


def test_a_camera_that_is_not_recorded_is_asked_once(tmp_path):
    calls = []
    rec = recorder(tmp_path, lambda r: calls.append(r) or httpx.Response(404))
    for s in (0, 120):
        rec.request(
            "overhead-1", "cam", "rtsp://m/bay/overhead-1", "crossing", T0 + timedelta(seconds=s)
        )
    rec._now = lambda: T0 + timedelta(minutes=10)
    assert [rec.cut(w) for w in rec.due()] == [None, None]
    assert len(calls) == 1


def api(status, seen=None) -> httpx.Client:
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={})

    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))


def spooled_clip(tmp_path) -> None:
    rec = recorder(tmp_path)
    rec.request("choke-1", "cam", URI, "crossing", T0)
    rec._now = lambda: T0 + timedelta(minutes=1)
    [w] = rec.due()
    rec.cut(w)


def test_an_uploaded_clip_leaves_the_spool(tmp_path):
    spooled_clip(tmp_path)
    seen = []
    assert EvidenceUploader(api(201, seen), tmp_path, "bay").drain()
    assert list(tmp_path.iterdir()) == []
    body = seen[0].content
    assert b'name="bay_id"' in body and b"ftypisom" in body and b'name="event_id"' in body


def test_while_the_api_is_away_the_clip_waits_with_the_same_event_id(tmp_path):
    spooled_clip(tmp_path)
    ids = []

    def record(status):
        def handler(request):
            ids.append(request.content.split(b'name="event_id"')[1][4:40])
            return httpx.Response(status, json={})

        return httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))

    for status in (503, 401):  # the API down, then this node's credential refused
        assert not EvidenceUploader(record(status), tmp_path, "bay").drain()
        assert len(list(tmp_path.glob("*.mp4"))) == 1
    assert EvidenceUploader(record(201), tmp_path, "bay").drain()
    assert len(set(ids)) == 1, "a retried clip must carry the same event id"


def test_a_rejected_clip_is_dropped_not_retried_forever(tmp_path):
    spooled_clip(tmp_path)
    assert EvidenceUploader(api(422), tmp_path, "bay").drain()
    assert list(tmp_path.iterdir()) == []


def test_the_spool_is_bounded_and_the_oldest_goes_first(tmp_path):
    for _ in range(3):
        spooled_clip(tmp_path)
    up = EvidenceUploader(api(503), tmp_path, "bay", max_bytes=len(MP4) * 2)
    up.enforce_limit()
    assert len(list(tmp_path.glob("*.mp4"))) == 2


def test_a_failure_to_ask_for_evidence_never_costs_a_count():
    counted = []

    class Downstream:
        def emit(self, e):
            counted.append(e)

    class Broken:
        def request(self, *a):
            raise RuntimeError("recorder broke")

    sink = EvidenceSink(Downstream(), Broken(), "crossing", {"choke-1": "cam"}, {"choke-1": URI})
    crossing = Crossing("choke-1", 1, CrossDirection.FORWARD, 0.9, T0, 12)
    sink.emit(crossing)
    assert counted == [crossing]
