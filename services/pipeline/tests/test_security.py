"""Security rules: when being somewhere becomes an incident, and when it must not.

The cost of a false incident is an operator who stops reading them, so most of these
tests are about what is *not* reported: a passer-by, a disarmed zone, the oven, a
frame where the question cannot be answered, a model that is not installed.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np

from ivaas_pipeline.security_runner import (
    HttpIncidentSink,
    SecurityConfig,
    SecurityConfigFeed,
    SecurityPipeline,
    snapshot,
)
from ivaas_pipeline.stages.security import SecurityRules, WatchZone
from ivaas_pipeline.stages.tracking import IouTracker
from ivaas_pipeline.types import Box, Detection, Frame, Track

T0 = datetime(2026, 9, 28, 23, 0, tzinfo=UTC)
SIZE = (1000, 1000)
LEFT_HALF = ((0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0))


def zone(rules=("intrusion",), *, armed=True, dwell=3.0, exclude=False, zid="z1", poly=LEFT_HALF):
    return WatchZone(zid, zid, poly, frozenset(rules), armed, dwell, exclude)


def person(track_id=1, x=200.0, conf=0.9) -> Track:
    # a person standing at x, feet at y=800
    return Track(track_id, Box(x - 40, 400, x + 40, 800), "person", conf)


def run(rules, seconds, people, zones, **kw):
    out = []
    for s in seconds:
        out += rules.update(
            at=T0 + timedelta(seconds=s),
            size=SIZE,
            people=people,
            hazards=kw.get("hazards", []),
            zones=zones,
            in_uniform=kw.get("in_uniform"),
            known_face=kw.get("known_face"),
        )
    return out


def test_a_passer_by_is_not_an_intrusion_but_someone_who_stays_is():
    r = SecurityRules()
    assert run(r, [0, 1, 2], [person()], [zone()]) == []
    found = run(r, [3.5], [person()], [zone()])
    assert [f.kind for f in found] == ["intrusion"]
    assert found[0].detail == {"track_id": 1, "dwell_s": 3.5}
    assert run(r, [4, 10, 60], [person()], [zone()]) == []  # once per person per zone


def test_leaving_and_returning_starts_the_dwell_again():
    r = SecurityRules()
    run(r, [0, 2], [person()], [zone()])
    run(r, [2.5], [], [zone()])  # stepped out
    assert run(r, [3, 4], [person()], [zone()]) == []  # back in: 1 s, not 4 s


def test_outside_the_zone_or_outside_armed_hours_says_nothing():
    r = SecurityRules()
    assert run(r, [0, 5], [person(x=800)], [zone()]) == []  # right half: not in the zone
    assert run(SecurityRules(), [0, 5], [person()], [zone(armed=False)]) == []


def test_an_ignored_area_hides_whoever_stands_in_it():
    r = SecurityRules()
    ignored = zone(
        (), exclude=True, zid="oven", poly=((0.1, 0.7), (0.3, 0.7), (0.3, 0.9), (0.1, 0.9))
    )
    assert run(r, [0, 5], [person(x=200)], [zone(), ignored]) == []


def test_a_badge_zone_reports_presence_for_the_api_to_check():
    found = run(SecurityRules(), [0, 5], [person()], [zone(("badge",))])
    assert [f.kind for f in found] == ["unbadged"]


def test_no_model_means_no_uniform_or_face_verdict():
    r = SecurityRules()
    assert run(r, [0, 5, 10], [person()], [zone(("ppe", "face"))]) == []


def test_no_uniform_needs_several_agreeing_reads():
    r = SecurityRules()
    no = lambda _p: (False, 0.9)
    assert run(r, [0, 3, 4], [person()], [zone(("ppe",))], in_uniform=no) == []  # 2 votes
    found = run(r, [5], [person()], [zone(("ppe",))], in_uniform=no)
    assert [f.kind for f in found] == ["no_ppe"]


def test_one_sighting_in_uniform_clears_the_person():
    r = SecurityRules()
    reads = iter([(True, 0.9)] + [(False, 0.9)] * 10)
    judge = lambda _p: next(reads)
    assert run(r, [0, 3, 4, 5, 6, 7], [person()], [zone(("ppe",))], in_uniform=judge) == []


def test_a_frame_without_a_clear_face_is_not_a_vote():
    r = SecurityRules()
    reads = iter([(None, 0), (False, 0.2), (None, 0), (False, 0.2), (False, 0.1)])
    judge = lambda _p: next(reads)
    assert run(r, [0, 3, 4, 5, 6], [person()], [zone(("face",))], known_face=judge) == []
    found = run(r, [7], [person()], [zone(("face",))], known_face=judge)
    assert [f.kind for f in found] == ["unknown_face"]  # the third unknown read


def fire(x=200.0, y=500.0, conf=0.8):
    return Detection(Box(x - 30, y - 30, x + 30, y + 30), "fire", conf)


def test_fire_needs_three_of_five_frames_then_stays_quiet():
    r = SecurityRules()
    z = [zone(("fire",))]
    frames = [[], [], [fire()], [], [fire()]]  # two hits: a flicker, not a fire
    assert [run(r, [i], [], z, hazards=h) for i, h in enumerate(frames)] == [[]] * 5
    found = run(r, [5], [], z, hazards=[fire()])  # the third hit in the last five frames
    assert [f.kind for f in found] == ["fire"]
    assert run(r, [6, 7, 8, 9, 10], [], z, hazards=[fire()]) == []  # cooldown
    assert [f.kind for f in run(r, [70, 71, 72], [], z, hazards=[fire()])] == ["fire"]


def test_the_oven_is_not_a_fire():
    r = SecurityRules()
    oven = zone((), exclude=True, zid="oven", poly=((0.1, 0.4), (0.3, 0.4), (0.3, 0.6), (0.1, 0.6)))
    assert run(r, range(10), [], [zone(("fire",)), oven], hazards=[fire(x=200, y=500)]) == []


# --- the pipeline around the rules --------------------------------------------------


class People:
    def __init__(self, boxes):
        self._boxes = boxes

    def detect(self, frame):
        return [Detection(b, "person", 0.9) for b in self._boxes] + [
            Detection(Box(0, 0, 10, 10), "truck", 0.9)  # other COCO classes are ignored
        ]


class Collect:
    def __init__(self):
        self.sent = []

    def send(self, path, body):
        self.sent.append((path, body))


def frames(n, step=1.0):
    return [
        Frame("yard-1", np.zeros((1000, 1000, 3), np.uint8), T0 + timedelta(seconds=i * step))
        for i in range(n)
    ]


def test_the_pipeline_sends_one_incident_with_evidence():
    delivery = Collect()
    cfg = SecurityConfig(zones={"yard-1": [zone()]})

    class Source:
        def frames(self):
            return iter(frames(6))

    sent = SecurityPipeline(
        source=Source(),
        people=People([Box(160, 400, 240, 800)]),
        tracker=IouTracker(),
        rules=SecurityRules(),
        config=lambda: cfg,
        sink=HttpIncidentSink(delivery, "bay-1", {"yard-1": "cam-uuid"}),
    ).run()

    assert sent == 1
    path, body = delivery.sent[0]
    assert path == "/api/v1/ingest/incidents"
    assert (
        body["camera_id"] == "cam-uuid" and body["zone_id"] == "z1" and body["kind"] == "intrusion"
    )
    assert base64.b64decode(body["snapshot_jpeg_b64"])[:2] == b"\xff\xd8"  # a JPEG


def test_a_camera_with_no_zones_runs_no_detection():
    class Boom:
        def detect(self, frame):
            raise AssertionError("detector should not run")

    class Source:
        def frames(self):
            return iter(frames(3))

    SecurityPipeline(
        source=Source(),
        people=Boom(),
        tracker=IouTracker(),
        rules=SecurityRules(),
        config=lambda: SecurityConfig(),
        sink=HttpIncidentSink(Collect(), "b", {}),
    ).run()


def test_snapshot_shrinks_large_frames():
    f = Frame("c", np.zeros((1080, 1920, 3), np.uint8), T0)
    raw = base64.b64decode(snapshot(f, Box(10, 10, 100, 100)))
    import cv2

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 960


def test_the_config_feed_maps_zones_to_camera_keys_and_keeps_the_last_on_error():
    body = {
        "zones": [
            {
                "id": "z1",
                "camera_id": "cam-uuid",
                "name": "Yard",
                "polygon": [[0, 0], [1, 0], [1, 1]],
                "rules": ["intrusion"],
                "armed": True,
                "min_dwell_s": 3,
                "exclude": False,
            },
            {
                "id": "z2",
                "camera_id": "other",
                "name": "Elsewhere",
                "polygon": [[0, 0], [1, 0], [1, 1]],
                "rules": ["fire"],
                "armed": True,
                "min_dwell_s": 0,
                "exclude": False,
            },
        ],
        "gallery": [{"name": "Ann", "embedding": [0.1] * 128}],
    }
    seen = []

    def respond(request):
        seen.append(dict(request.url.params))
        return next(calls)

    calls = iter([httpx.Response(200, json=body), httpx.Response(503)])
    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond))
    feed = SecurityConfigFeed(
        client, "bay-1", {"cam-uuid": "yard-1"}, capabilities=("people", "fire")
    )

    assert feed.refresh()
    assert seen[0] == {
        "bay_id": "bay-1",
        "capabilities": "people,fire",
    }  # the edge says what it runs
    cfg = feed.current()
    assert [z.name for z in cfg.zones["yard-1"]] == ["Yard"]  # other cameras' zones dropped
    assert cfg.gallery[0][0] == "Ann" and cfg.gallery[0][1].shape == (128,)
    assert not feed.refresh()
    assert feed.current() is cfg  # an API hiccup does not disarm the site
