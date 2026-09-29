"""Security rules: who is where, for how long, and what that means for each zone.

Pure logic over tracked people and hazard detections; the models, the camera and the
API are all outside. Nothing here is certain from one frame, so every judgement needs
agreement across several:

- a person counts once they have stayed `min_dwell_s` inside an armed zone (a
  passer-by is not an intrusion);
- "no uniform" and "unknown face" need VOTES agreeing reads; one "yes" clears the
  person for that zone, and a frame where the question cannot be answered (no face
  visible, no model installed) is not a vote either way;
- fire and smoke need HAZARD_HITS of the last HAZARD_WINDOW frames, then stay quiet
  for COOLDOWN, because a flicker of orange is not a fire and a fire is not news twice
  a second.

Where a person stands is their foot point: the middle of the bottom of their box,
which is on the floor the zone was drawn on.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ivaas_pipeline.types import Box, Detection, Track

VOTES = 3
HAZARD_WINDOW = 5
HAZARD_HITS = 3
COOLDOWN = timedelta(seconds=60)
PERSON_RULES = ("intrusion", "badge", "ppe", "face")
KIND_OF = {"intrusion": "intrusion", "badge": "unbadged", "ppe": "no_ppe", "face": "unknown_face"}


@dataclass(frozen=True)
class WatchZone:
    id: str
    name: str
    polygon: tuple[tuple[float, float], ...]  # normalised 0..1
    rules: frozenset[str]
    armed: bool = True
    min_dwell_s: float = 3.0
    exclude: bool = False

    def contains(self, x: float, y: float) -> bool:
        inside = False
        pts = self.polygon
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside


@dataclass(frozen=True)
class Finding:
    zone_id: str
    kind: str  # the API's IncidentKind value
    confidence: float
    box: Box
    detail: dict = field(default_factory=dict)


#: (verdict, confidence) for one person in one frame; verdict None means "cannot tell"
Judge = Callable[[Track], tuple[bool | None, float]]


class SecurityRules:
    def __init__(self) -> None:
        self._since: dict[tuple[int, str], datetime] = {}
        self._settled: set[tuple[int, str, str]] = set()
        self._votes: dict[tuple[int, str, str], int] = {}
        self._hazard: dict[tuple[str, str], deque[bool]] = {}
        self._quiet_until: dict[tuple[str, str], datetime] = {}

    def update(
        self,
        *,
        at: datetime,
        size: tuple[int, int],
        people: list[Track],
        hazards: list[Detection],
        zones: list[WatchZone],
        in_uniform: Judge | None = None,
        known_face: Judge | None = None,
    ) -> list[Finding]:
        w, h = size
        ignored = [z for z in zones if z.exclude]
        watched = [z for z in zones if not z.exclude and z.armed and z.rules]
        findings: list[Finding] = []

        def spot(box: Box, foot: bool) -> tuple[float, float] | None:
            x = (box.x1 + box.x2) / 2 / w
            y = (box.y2 if foot else (box.y1 + box.y2) / 2) / h
            return None if any(z.contains(x, y) for z in ignored) else (x, y)

        # --- people ---------------------------------------------------------------
        present: set[tuple[int, str]] = set()
        for person in people:
            where = spot(person.box, foot=True)
            if where is None:
                continue
            for zone in watched:
                rules = zone.rules.intersection(PERSON_RULES)
                if not rules or not zone.contains(*where):
                    continue
                key = (person.track_id, zone.id)
                present.add(key)
                since = self._since.setdefault(key, at)
                dwell = (at - since).total_seconds()
                if dwell < zone.min_dwell_s:
                    continue
                for rule in sorted(rules):
                    done = (person.track_id, zone.id, rule)
                    if done in self._settled:
                        continue
                    if rule in ("intrusion", "badge"):
                        verdict, conf = False, person.confidence
                    else:
                        judge = in_uniform if rule == "ppe" else known_face
                        if judge is None:
                            continue  # no model for this rule: say nothing, not "fine"
                        verdict, conf = judge(person)
                        if verdict is None:
                            continue  # could not tell this frame
                        if verdict:
                            self._settled.add(done)  # seen in uniform / recognised: cleared
                            continue
                        self._votes[done] = self._votes.get(done, 0) + 1
                        if self._votes[done] < VOTES:
                            continue
                    self._settled.add(done)
                    findings.append(
                        Finding(
                            zone.id,
                            KIND_OF[rule],
                            conf,
                            person.box,
                            {"track_id": person.track_id, "dwell_s": round(dwell, 1)},
                        )
                    )
        # someone who leaves and comes back has to dwell again
        for key in list(self._since):
            if key not in present:
                del self._since[key]

        # --- fire and smoke -------------------------------------------------------
        for zone in (z for z in watched if "fire" in z.rules):
            for label in ("fire", "smoke"):
                hits = [
                    d
                    for d in hazards
                    if d.label == label
                    and (p := spot(d.box, foot=False)) is not None
                    and zone.contains(*p)
                ]
                key = (zone.id, label)
                window = self._hazard.setdefault(key, deque(maxlen=HAZARD_WINDOW))
                window.append(bool(hits))
                if key in self._quiet_until and at < self._quiet_until[key]:
                    continue  # already raised; the same fire is not news again yet
                if sum(window) < HAZARD_HITS or not hits:
                    continue
                best = max(hits, key=lambda d: d.confidence)
                self._quiet_until[key] = at + COOLDOWN
                window.clear()
                findings.append(
                    Finding(
                        zone.id,
                        label,
                        best.confidence,
                        best.box,
                        {"frames": f"{HAZARD_HITS} of {HAZARD_WINDOW}"},
                    )
                )
        return findings
