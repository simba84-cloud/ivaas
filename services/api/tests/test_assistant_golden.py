"""T6.6: the assistant's figures are the reports' figures, for every golden question.

Each golden question names the tool a model should call for it and the figure the
answer must carry. The figure is taken twice: from the tool, as the model would see
it, and from what people see (the daily report's CSV and PDF, the Balances page,
the exceptions list). They must be equal, and the tool result must reach the model
whole.

With IVAAS_EVAL_LLM_URL set (e.g. http://localhost:11434 for the stack's Ollama),
the same questions are put to a real model and each reply must state its figure.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from pypdf import PdfReader
from test_reports import DAY, day  # noqa: F401  (the scenario: one Harare site's day)

from ivaas.application.assistant import MAX_TOOL_RESULT_CHARS
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.tenancy import tenant_context

MANIFEST = (
    "date,plate,direction,expected,reference,route\n2026-10-01,ABC 1001,loading,104,M-1,Route 7\n"
)


@dataclass(frozen=True)
class Golden:
    question: str
    tool: str
    args: dict[str, Any]
    #: the figure, from the tool result
    said: Any
    #: the same figure, from what people see
    shown: Any
    #: how a reply states it
    words: Any


def _csv(c, admin, site) -> list[dict]:
    r = c.get(
        "/api/v1/reports/daily",
        params={"site_id": site, "day": DAY, "format": "csv"},
        headers=admin,
    )
    return list(csv.DictReader(io.StringIO(r.text)))


def _pdf(c, admin, site) -> str:
    r = c.get("/api/v1/reports/daily", params={"site_id": site, "day": DAY}, headers=admin)
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)


def _settled(rows, direction):
    return sum(
        int(r["count_of_record"])
        for r in rows
        if r["direction"] == direction and r["status"] != "open"
    )


GOLDEN = [
    Golden(
        "How many crates were dispatched on 1 October?",
        "daily_report",
        {"day": DAY},
        lambda t: t["dispatched"],
        lambda s: _settled(s.rows, "loading"),
        lambda v: [f"{v}", f"{v:,}"],
    ),
    Golden(
        "How many crates came back on 1 October?",
        "daily_report",
        {"day": DAY},
        lambda t: t["returned"],
        lambda s: _settled(s.rows, "offloading"),
        lambda v: [f"{v}"],
    ),
    Golden(
        "How many crates are still outstanding from 1 October?",
        "daily_report",
        {"day": DAY},
        lambda t: t["outstanding"],
        lambda s: _settled(s.rows, "loading") - _settled(s.rows, "offloading"),
        lambda v: [f"{v}"],
    ),
    Golden(
        "What was the counting accuracy on 1 October?",
        "daily_report",
        {"day": DAY},
        lambda t: t["accuracy"]["mean_accuracy_pct"],
        lambda s: 98.0 if "98.0%" in s.pdf else None,
        lambda v: [f"{v}%", f"{v:.0f}%"],
    ),
    Golden(
        "How many loads on 1 October had no number plate?",
        "daily_report",
        {"day": DAY},
        lambda t: t["without_a_plate"],
        lambda s: sum(1 for r in s.rows if not r["plate"]),
        lambda v: [f"{v}", "one"],
    ),
    Golden(
        "Is any truck still at the bay from 1 October?",
        "daily_report",
        {"day": DAY},
        lambda t: t["still_at_the_bay"],
        lambda s: sum(1 for r in s.rows if r["status"] == "open"),
        lambda v: ["ABC 1003"],
    ),
    Golden(
        "Which loads on 1 October were corrected by a person, and to what?",
        "daily_report",
        {"day": DAY},
        lambda t: [(x["plate"], x["ai_count"], x["corrected_to"]) for x in t["corrections"]],
        lambda s: [
            (r["plate"], int(r["ai_count"]), int(r["corrected_count"]))
            for r in s.rows
            if r["corrected_count"]
        ],
        lambda v: ["78"],
    ),
    Golden(
        "Did any load on 1 October disagree with the manifest?",
        "daily_report",
        {"day": DAY},
        lambda t: [(x["plate"], x["manifest"], x["counted"]) for x in t["manifest_exceptions"]],
        lambda s: [(e["plate"], e["expected"], e["counted"]) for e in s.exceptions],
        lambda v: ["104"],
    ),
    Golden(
        "Which trucks still have crates out this week?",
        "balances",
        {"days": 7, "by": "truck"},
        lambda t: {r["plate"]: r["outstanding"] for r in t["rows"]},
        lambda s: {b["key"] or None: b["outstanding"] for b in s.by_truck},
        lambda v: ["ABC 1002", "78"],
    ),
    Golden(
        "How many crates went out and came back each day this week?",
        "balances",
        {"days": 7, "by": "day"},
        lambda t: {r["day"]: (r["dispatched"], r["returned"]) for r in t["rows"]},
        lambda s: {b["key"]: (b["dispatched"], b["returned"]) for b in s.by_day},
        lambda v: ["190", "30"],
    ),
]


@dataclass
class Shown:
    rows: list[dict]
    pdf: str
    exceptions: list[dict]
    by_truck: list[dict]
    by_day: list[dict]


@pytest.fixture
def world(day):  # noqa: F811
    c, admin, bay, clock = day
    r = c.post(
        "/api/v1/manifests/import",
        files={"file": ("m.csv", MANIFEST.encode())},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["exceptions_raised"] == 1, r.text
    shown = Shown(
        rows=_csv(c, admin, bay["site_id"]),
        pdf=_pdf(c, admin, bay["site_id"]),
        exceptions=c.get("/api/v1/exceptions", headers=admin).json(),
        by_truck=c.get("/api/v1/balances", params={"by": "truck"}, headers=admin).json(),
        by_day=c.get("/api/v1/balances", params={"by": "day"}, headers=admin).json(),
    )
    container = c.app.state.container

    def ask(tool, args):
        async def run():
            with tenant_context(BAKERS_INN_ID):
                return await (await container.analytics_tools()).call(tool, args)

        return c.portal.call(run)

    return c, admin, shown, ask


@pytest.mark.parametrize("g", GOLDEN, ids=[g.question for g in GOLDEN])
def test_the_assistant_gives_the_reports_figures(world, g):
    _, _, shown, ask = world
    result = ask(g.tool, g.args)
    assert "error" not in result, result
    assert g.said(result) == g.shown(shown), g.question
    # the model reads the result as JSON cut at a limit: all of it must reach the model
    assert len(json.dumps(result, default=str)) <= MAX_TOOL_RESULT_CHARS


def test_yesterday_and_today_are_the_sites_days(world):
    c, _, _, ask = world
    clock = c.app.state.container.clock
    # 23:30 UTC on 1 October is already 2 October in Harare
    clock.at = datetime(2026, 10, 1, 23, 30, tzinfo=UTC)
    assert ask("daily_report", {"day": "yesterday"})["day"] == "2026-10-01"
    today = ask("daily_report", {})
    assert today["day"] == "2026-10-02" and today["day_still_running"]
    assert ask("daily_report", {"day": "the day after"})["error"]
    assert ask("daily_report", {"site": "Nowhere"})["error"] == "no site by that name"


def test_a_day_without_tally_sheets_has_no_accuracy_rather_than_zero(world):
    _, _, _, ask = world
    empty = ask("daily_report", {"day": "2026-09-01"})
    assert empty["loads"] == 0 and empty["accuracy"]["mean_accuracy_pct"] is None
    assert "no accuracy figure" in empty["accuracy"]["note"]


# --- the same questions, to a real model ------------------------------------------
LIVE = os.environ.get("IVAAS_EVAL_LLM_URL")


@pytest.mark.skipif(not LIVE, reason="set IVAAS_EVAL_LLM_URL to put the golden set to a model")
def test_a_real_model_answers_the_golden_set_with_the_reports_figures(world):
    from ivaas.adapters.llm.openai_compatible import OpenAiCompatibleChatModel

    c, admin, shown, _ = world
    model = os.environ.get("IVAAS_EVAL_LLM_MODEL", "qwen3:8b")
    c.app.state.container.chat_model = OpenAiCompatibleChatModel(LIVE, model, timeout_s=300)
    missed = []
    for g in GOLDEN:
        r = c.post(
            "/api/v1/assistant/chat",
            json={"messages": [{"role": "user", "content": g.question}]},
            headers=admin,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        wanted = g.words(g.shown(shown))
        tools = [t["name"] for t in body["tools_used"]]
        ok = g.tool in tools and any(w in body["reply"] for w in wanted)
        print(f"\n[{'ok' if ok else 'MISS'}] {g.question}\n  tools={tools}\n  {body['reply']}")
        if not ok:
            missed.append(g.question)
    assert not missed, f"{len(missed)} of {len(GOLDEN)} missed: {missed}"
