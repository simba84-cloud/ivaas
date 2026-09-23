"""Write the narrative summary for a finished analysis report.

The model gets the report as data and a strict brief. It cannot call tools or see
anything else, so every figure in the summary has to come from the report. If the
model is unavailable the report simply has no summary; the numbers stand alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ivaas.domain.analysis import AnalysisJob
from ivaas.ports.assistant import ChatMessage, ChatModel, ChatModelUnavailableError

log = logging.getLogger(__name__)

BRIEF = """You write the summary section of a crate-counting report for a bakery's despatch team.
You are given the report as JSON. Write 3-6 sentences of plain English for a supervisor.

Rules:
- Use ONLY figures from the JSON. Never invent, estimate or round differently.
- Say how many truck loads, stacks and crates were counted, and the plate(s) if present.
- Mention anything a supervisor should check: loads with low-confidence stacks, a load
  with no plate read, unusually small stacks, a video with no loading at all.
- State the video length. Do not describe the pipeline or the model.
- No headings, no bullet points, no preamble like "Summary:"."""


def _fmt(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def report_for_model(job: AnalysisJob) -> dict:
    return {
        "video": job.filename,
        "video_length": _fmt(job.duration_s or 0),
        "total_crates": job.total_crates,
        "total_stacks": sum(ld.stacks for ld in job.loads),
        "loads": [
            {
                "number": i,
                "plate": ld.plate,
                "from": _fmt(ld.start_s),
                "to": _fmt(ld.end_s),
                "stacks": ld.stacks,
                "crates": ld.crates,
                "low_confidence_stacks": ld.low_confidence,
                "stack_sizes": [
                    int(e.detail.split()[2])
                    for e in job.timeline
                    if e.kind == "stack_counted" and ld.start_s <= e.at_s <= ld.end_s
                ],
            }
            for i, ld in enumerate(job.loads, start=1)
        ],
    }


@dataclass
class SummariseReport:
    model: ChatModel | None

    async def __call__(self, job: AnalysisJob) -> str | None:
        if self.model is None:
            return None
        messages = [
            ChatMessage("system", BRIEF),
            ChatMessage("user", json.dumps(report_for_model(job), indent=1)),
        ]
        try:
            reply = await self.model.complete(messages, tools=[])
        except ChatModelUnavailableError as exc:
            log.warning("no summary for %s: %s", job.id, exc)
            return None
        text = reply.content.strip()
        return text or None
