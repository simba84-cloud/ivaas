from datetime import UTC, datetime, timedelta

import numpy as np

from ivaas_pipeline.runner import LprPipeline
from ivaas_pipeline.stages.plates import PlateNormaliser, PlateVoter
from ivaas_pipeline.types import Box, Frame, PlateCandidate

T0 = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
BLANK = np.zeros((4, 4, 3), np.uint8)


def cand(text, conf=0.95):
    return PlateCandidate(text, conf, Box(0, 0, 10, 10))


def frame(i, camera="lpr-1"):
    return Frame(camera, BLANK, T0 + timedelta(milliseconds=200 * i))


def test_normaliser_formats_real_plates_and_rejects_junk_seen_on_site():
    n = PlateNormaliser()
    assert n.normalise(cand("ABC 1234", 0.97)) == "ABC 1234"
    assert n.normalise(cand("abc-1234", 0.97)) == "ABC 1234"
    for text, conf in [
        ("AGA3", 0.93),
        ("47895", 0.90),
        ("GT673", 0.70),
        ("R48499A", 0.54),
        ("JDN3346", 0.67),
        ("AGM4425", 0.74),
        ("IO7192", 0.58),
    ]:
        assert n.normalise(cand(text, conf)) is None, text


def test_voter_needs_agreeing_reads_and_outvotes_a_confident_misread():
    v = PlateVoter(min_reads=3, window=timedelta(seconds=5))
    reads = ["ABD 5670", "ABD5679", "ABD 5670", "ABD 5670"]  # one 0->9 misread at high confidence
    events = []
    for i, r in enumerate(reads):
        events += v.update(frame(i), [cand(r, 0.96)])
    assert [(e.plate, e.reads) for e in events] == [("ABD 5670", 3)]
    assert events[0].camera_id == "lpr-1"


def test_voter_emits_once_per_cooldown_then_again_for_the_next_visit():
    v = PlateVoter(min_reads=2, window=timedelta(seconds=5), cooldown=timedelta(minutes=2))
    events = []
    for i in range(40):  # truck idles at the bay for 8 s
        events += v.update(frame(i), [cand("ABC 1234")])
    assert len(events) == 1
    later = T0 + timedelta(minutes=3)
    for i in range(3):
        events += v.update(Frame("lpr-1", BLANK, later + timedelta(seconds=i)), [cand("ABC 1234")])
    assert len(events) == 2


def test_stale_reads_fall_out_of_the_window():
    v = PlateVoter(min_reads=3, window=timedelta(seconds=2))
    assert v.update(frame(0), [cand("ABC 1234")]) == []
    assert v.update(frame(1), [cand("ABC 1234")]) == []
    late = Frame("lpr-1", BLANK, T0 + timedelta(seconds=30))
    assert v.update(late, [cand("ABC 1234")]) == []  # the two earlier reads expired


def test_two_trucks_in_view_are_both_reported():
    v = PlateVoter(min_reads=2)
    events = []
    for i in range(3):
        events += v.update(frame(i), [cand("ABC 1234"), cand("ABD 5670")])
    assert sorted(e.plate for e in events) == ["ABC 1234", "ABD 5670"]


def test_lpr_pipeline_end_to_end():
    class Source:
        def frames(self):
            return (frame(i) for i in range(10))

    class Reader:
        def read(self, f):
            return [cand("ABC 1234")]

    class Collect:
        def __init__(self):
            self.items = []

        def emit(self, e):
            self.items.append(e)

    sink = Collect()
    assert LprPipeline(Source(), Reader(), PlateVoter(), sink).run() == 1
    assert sink.items[0].plate == "ABC 1234" and sink.items[0].reads == 3
