import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ivaas.application.summarise import SummariseReport, report_for_model
from ivaas.domain.analysis import AnalysisJob, DetectedLoad, TimelineEvent
from ivaas.ports.assistant import ChatMessage, ChatModelUnavailableError


def job():
    j = AnalysisJob(uuid4(), "shift.mp4", "k", "op", datetime.now(UTC))
    j.finish(
        datetime.now(UTC),
        [DetectedLoad(30, 90, 2, 27, 1, "ABC 1234"), DetectedLoad(300, 310, 1, 5, 0, None)],
        [
            TimelineEvent(30, "stack_counted", "Stack of 14 crates"),
            TimelineEvent(60, "stack_counted", "Stack of 13 crates"),
            TimelineEvent(305, "stack_counted", "Stack of 5 crates"),
        ],
        400.0,
    )
    return j


def test_report_for_model_is_complete_and_only_from_the_job():
    r = report_for_model(job())
    assert r["total_crates"] == 32 and r["total_stacks"] == 3 and r["video_length"] == "6:40"
    assert r["loads"][0]["stack_sizes"] == [14, 13] and r["loads"][0]["low_confidence_stacks"] == 1
    assert r["loads"][1]["plate"] is None and r["loads"][1]["stack_sizes"] == [5]


class Model:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.seen = reply, fail, None

    async def complete(self, messages, tools):
        self.seen = messages
        if self.fail:
            raise ChatModelUnavailableError("down")
        return ChatMessage("assistant", self.reply)


@pytest.mark.asyncio
async def test_summary_comes_from_the_model_with_the_report_as_data():
    m = Model("Two loads, 32 crates.")
    assert await SummariseReport(m)(job()) == "Two loads, 32 crates."
    assert m.seen[0].role == "system" and json.loads(m.seen[1].content)["total_crates"] == 32
    assert not any(k for k in m.seen if k.role == "tool")


@pytest.mark.asyncio
async def test_no_model_or_failed_model_means_no_summary_not_an_error():
    assert await SummariseReport(None)(job()) is None
    assert await SummariseReport(Model(fail=True))(job()) is None
    assert await SummariseReport(Model("   "))(job()) is None
