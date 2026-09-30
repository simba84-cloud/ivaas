import json
from datetime import UTC, datetime

import httpx

from ivaas_pipeline.adapters.delivery import SpooledDelivery
from ivaas_pipeline.adapters.http_sink import HttpCrossingSink, HttpPlateSink
from ivaas_pipeline.types import CrossDirection, Crossing, PlateEvent

CAMS = {"choke-1": "cam-uuid", "lpr-1": "lpr-uuid"}


def crossing(n, crates=14):
    return Crossing(
        "choke-1", n, CrossDirection.FORWARD, 0.9, datetime(2026, 9, 22, tzinfo=UTC), crates
    )


class FakeApi:
    def __init__(self):
        self.up, self.received = True, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-ivaas-key"] == "k"
        if not self.up:
            raise httpx.ConnectError("down")
        self.received.append(json.loads(request.content))
        return httpx.Response(200, json={})


def delivery(tmp_path, handler, **kw):
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://api",
        headers={"X-IVaaS-Key": "k"},
    )
    return SpooledDelivery("http://api", "k", tmp_path / "s.spool", client=client, **kw)


def test_delivers_with_stack_count_and_api_key(tmp_path):
    api = FakeApi()
    d = delivery(tmp_path, api.handler)
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(1))
    assert api.received[0]["crates"] == 14 and api.received[0]["direction"] == "loading"
    assert api.received[0]["camera_id"] == "cam-uuid"
    assert d.pending == 0 and not (tmp_path / "s.spool").read_text().strip()


def test_outage_spools_in_order_and_replays_after_restart(tmp_path):
    api = FakeApi()
    sink = HttpCrossingSink(delivery(tmp_path, api.handler), "bay", CAMS)
    api.up = False
    for n in range(3):
        sink.emit(crossing(n))
    assert api.received == []
    assert len((tmp_path / "s.spool").read_text().splitlines()) == 3

    # edge node reboots while the API is still down, then the API comes back
    d2 = delivery(tmp_path, api.handler)
    assert d2.pending == 3
    api.up = True
    HttpCrossingSink(d2, "bay", CAMS).emit(crossing(99))
    assert [r["track_id"] for r in api.received] == [0, 1, 2, 99]  # order preserved
    assert d2.pending == 0


def test_client_errors_are_dropped_not_retried_forever(tmp_path):
    d = delivery(tmp_path, lambda r: httpx.Response(422, text="bad body"))
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(1))
    assert d.pending == 0


def test_a_refused_credential_keeps_the_counts_for_the_next_enrolment(tmp_path):
    """401 is not a bad event: the node was revoked or re-enrolled. Dropping would lose
    real crates; keeping them lets the node, enrolled again, deliver them in order."""
    d = delivery(tmp_path, lambda r: httpx.Response(401, json={"detail": "invalid"}))
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(1))
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(2))
    assert d.pending == 2

    api = FakeApi()
    again = delivery(tmp_path, api.handler)  # restarted with a working credential
    again.flush()
    assert [r["track_id"] for r in api.received] == [1, 2]


def test_spool_is_bounded(tmp_path):
    api = FakeApi()
    api.up = False
    d = delivery(tmp_path, api.handler, max_spool=5)
    sink = HttpCrossingSink(d, "bay", CAMS)
    for n in range(8):
        sink.emit(crossing(n))
    assert d.pending == 5


def test_corrupt_spool_does_not_crash_startup(tmp_path):
    (tmp_path / "s.spool").write_text("{not json\n")
    assert delivery(tmp_path, FakeApi().handler).pending == 0


def test_plates_and_crossings_share_one_ordered_spool(tmp_path):
    api = FakeApi()
    d = delivery(tmp_path, api.handler)
    api.up = False
    HttpPlateSink(d, "bay", CAMS).emit(
        PlateEvent("lpr-1", "ABC 1234", 0.96, 4, datetime(2026, 9, 22, tzinfo=UTC))
    )
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(1))
    api.up = True
    d.flush()
    assert [r.get("plate", r.get("track_id")) for r in api.received] == ["ABC 1234", 1]
    assert api.received[0]["camera_id"] == "lpr-uuid"


def test_every_event_carries_an_id_that_survives_a_restart(tmp_path):
    """The id is fixed when the event is spooled, so a resend after a crash is the same
    event to the API, which skips it rather than counting the crates twice."""
    api = FakeApi()
    api.up = False
    d = delivery(tmp_path, api.handler)
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(1))
    spooled = d._queue[0]["body"]["event_id"]

    api.up = True
    delivery(tmp_path, api.handler).flush()  # the node restarted
    assert api.received[0]["event_id"] == spooled


def test_delivery_metrics_account_for_every_event(tmp_path):
    from prometheus_client import REGISTRY

    def value(name, **labels):
        return REGISTRY.get_sample_value(name, labels) or 0.0

    path = "/api/v1/ingest/crossings"
    before = (
        value("ivaas_delivery_queued_total", path=path),
        value("ivaas_delivery_delivered_total", path=path),
        value("ivaas_delivery_dropped_total", reason="rejected"),
    )
    api = FakeApi()
    d = delivery(tmp_path, api.handler)
    HttpCrossingSink(d, "bay", CAMS).emit(crossing(1))
    rejecting = delivery(tmp_path / "b", lambda r: httpx.Response(422))
    HttpCrossingSink(rejecting, "bay", CAMS).emit(crossing(2))
    after = (
        value("ivaas_delivery_queued_total", path=path),
        value("ivaas_delivery_delivered_total", path=path),
        value("ivaas_delivery_dropped_total", reason="rejected"),
    )
    assert [a - b for a, b in zip(after, before, strict=True)] == [2, 1, 1]
    assert value("ivaas_delivery_pending") == 0


def test_in_the_background_a_black_holed_api_never_holds_up_counting(tmp_path):
    """A WAN outage that swallows packets must not stall the camera threads: a stalled
    counter skips frames, and the outage would lose crates instead of delaying them."""
    import threading
    import time

    gate = threading.Event()
    received = []

    def hanging(request: httpx.Request) -> httpx.Response:
        gate.wait(timeout=10)  # the link is black-holed until the gate opens
        received.append(json.loads(request.content)["track_id"])
        return httpx.Response(200, json={})

    d = SpooledDelivery(
        "http://api",
        "k",
        tmp_path / "spool",
        client=httpx.Client(base_url="http://api", transport=httpx.MockTransport(hanging)),
        background=True,
    )
    sink = HttpCrossingSink(d, "bay", CAMS)
    started = time.monotonic()
    for n in range(20):
        sink.emit(crossing(n))
    assert time.monotonic() - started < 0.5, "emitting waited on the network"
    assert d.pending >= 19

    gate.set()  # the link comes back
    deadline = time.monotonic() + 5
    while d.pending and time.monotonic() < deadline:
        time.sleep(0.02)
    d.close()
    assert d.pending == 0
    assert received == list(range(20)), "delivered out of order or with losses"
