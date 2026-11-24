# NFL Snacks & Treats: product counting module

| Item | Detail |
| --- | --- |
| Document date | 9 November 2026 |
| Status | **Draft for design review.** Not built. Camera points as specified in June 2026; the site has not yet been surveyed. |
| Client / platform | NFL Snacks & Treats / Cassava (IVaaS, Video Analytics as a Service) |
| Branch | `feature/nfl-snacks-counting-module` |
| Related | [ADR 0002: product type, not crate](../adr/0002-product-type-generalisation.md); `docs/IVaaS_Use_Cases.docx` (UC-01 to UC-23, Bakers Inn) |

Anything this document does not know is marked **TBC** and listed in §9. No figure here
is a guess. A target that NFL has not agreed is not written down as one.

## 1. Purpose

Cassava's project sponsor asked (2 November 2026) for the NFL camera-counting engagement to be
documented as the next IVaaS module. Existing platform capabilities can support it,
and it should run alongside the Bakers Inn work, which is already in progress.

This document:
- records what NFL needs;
- maps each need to what IVaaS already does, and lists the gaps;
- sets out the questions the site visit must answer before anything is built.

## 2. Business problem

NFL produces snacks and treats and needs an independent, automatic count of product
at four points in its production and dispatch flow: a counting point, an operator
handling area, a collection/picking area and a conveyor belt. The AI count is to be
checked against a reference count, as it is at Bakers Inn. What that reference is
(production records, a manual tally, dispatch notes) is **TBC** (§9, Q2).

As with Bakers Inn, **the figure is the product**:
- a person's correction is recorded, but never improves the AI's accuracy score;
- a point that was not measured shows as "not measured", never as zero.

## 3. Stakeholders and actors

| Party | Role | Part in this module |
| --- | --- | --- |
| NFL Snacks & Treats | Client sponsor | Confirmed interest in proceeding (Oct 2026) |
| NFL Snacks & Treats | Site contact | Site-visit coordination |
| Cassava | Account management | NFL account contacts (from Oct 2026) |
| Cassava | Requirement owner | Original requirement and camera quotation request (Jun 2026) |
| Cassava | Project sponsor | Direction to build this as a VaaS module |
| Cassava | Technical adviser | Consulted through design and testing |
| Cassava | AI Solutions Engineering | Build, train, test and deploy |

The system actors are the existing scoped roles in `services/api/src/ivaas/domain/rbac.py`:
- `tenant_owner` and `tenant_admin` (NFL administration);
- `site_manager` (reconciliation, reports, corrections);
- `bay_operator`, reused here as a **line operator**: an IVaaS "bay" is any counting location, not only a loading bay;
- `auditor`, `integration`, and the Cassava platform roles;
- the edge AI pipeline, as a system actor.

## 4. Camera placement

Four Hikvision 4MP cameras, mounted **top-down** to see product from above, as
specified in the June 2026 quotation request. Each camera streams RTSP into MediaMTX
and is registered like any other IVaaS camera (UC-14, `services/api/src/ivaas/application/cameras.py`).

| # | Point | What the camera must see | Mount | Status |
| --- | --- | --- | --- | --- |
| C1 | Counting point | Each unit as it is counted, with nothing hiding it | Top-down | Specified, not surveyed |
| C2 | Operator handling area | Product being handled, and the operator's actions on it | Top-down | Specified, not surveyed |
| C3 | Collection / picking area | Units being picked or set down for collection | Top-down | Specified, not surveyed |
| C4 | Conveyor belt | Every item on the belt, across its full width, at the speed it moves | Top-down | Specified, not surveyed |

The site survey must record for each point: mount height, field of view, lighting
(including glare off packaging), the frame rate the motion needs, power and network
reach, and where the edge node sits. All are **TBC**.

## 5. Counting objectives and how IVaaS meets them

The pipeline stages that count already exist in
`services/pipeline/src/ivaas_pipeline/stages/`. Which one suits each point depends on
how product moves there, and that is not yet confirmed. The table below gives
**candidates**, not decisions.

| Point | Objective (draft) | Candidate counter | Why |
| --- | --- | --- | --- |
| C4 Conveyor | Count each item that passes | `LineCrossingCounter` (`counting.py`) | One count per tracked item crossing a line drawn across the belt. Selected per deployment in `__main__.py` when `"count"` is not `"stack"`. |
| C1 Counting point | Count units as they leave the point | `LineCrossingCounter`, or `StackCrossingCounter` (`stack_counting.py`) if units are stacked | A stacked unit carries a layer count; a single unit counts as one. |
| C3 Picking area | Count units set down for collection | `StackSettleCounter` (`settle_counting.py`) or `StackPresenceCounter` (`presence_counting.py`) | Counts what is set down and stays put, or sits in a zone long enough, not what is just carried past. |
| C2 Operator handling | **TBC:** count units, or watch for exceptions (product removed or dropped) | None chosen | Needs NFL to say what it wants from this point. |

**Two cameras on one flow.** If two cameras watch the same flow, the existing fusion
stage (`fusion.py`) removes double counts, as it does for the two Bakers Inn
chokepoint cameras.

**Speed.** A conveyor may move faster and carry denser items than a loading bay. The
tracker (`tracking.py`) and frame stride were tuned for crates on dollies. Whether
they keep up is measured on NFL footage, not assumed.

## 6. Business requirements and success criteria

These are written in the same shape as Bakers Inn's. **The targets are NFL's to set**
and are not copied from Bakers Inn.

| Requirement | Target |
| --- | --- |
| Counting accuracy, AI count against the reference count | **TBC with NFL** |
| No slowdown to production or picking | Required; how it is measured is **TBC** |
| Continuous running before acceptance | **TBC with NFL** |
| Each count is attributable to a point, a time and (if NFL needs it) a batch, shift or order | **TBC:** which of these NFL needs |
| Evidence: a clip for each disputed count | Reuse UC-13 |
| Reports: daily, plus accuracy export (PDF/Excel) | Reuse UC-09, and the accuracy export already built |

## 7. Reuse and new work

**Reused as is.** No new code; NFL is configuration:

| Capability | Where |
| --- | --- |
| NFL as its own tenant, isolated by row-level security | `domain/tenancy.py`, ADR 0001 |
| Users, roles, SSO | UC-19, `application/users.py`, `application/sso.py` |
| Camera registration and discovery | UC-14, `application/cameras.py` |
| Edge node enrolment and model rollout | UC-15, UC-16, `domain/edge.py`, `domain/ml_models.py` |
| Detect → track → count → fuse pipeline | `services/pipeline/src/ivaas_pipeline/stages/` |
| Counting sessions and reconciliation against a reference count | UC-03, UC-04, `application/sessions.py`, `application/tally.py` |
| Corrections recorded without rewriting the AI count | UC-05 |
| Evidence clips, daily reports, accuracy export, webhooks | UC-13, UC-09, UC-12 |
| Audit log of every count and setup change | `domain/audit.py` |
| Live overview and camera health | UC-17, `application/overview.py` |
| Billing and entitlements | UC-21, `application/billing.py` |

Paths are relative to `services/api/src/ivaas/` unless stated.

**New work:**

1. **A detector for NFL's products.** It is trained on labelled NFL footage through
   `ml/`, exported to ONNX, and rolled out like `models/stacks-v2.onnx`. This cannot
   start until footage exists.
2. **Product type and unit instead of "crate".** Counts, reports and the portal must
   say "packets" or "cartons" where NFL needs them to; see ADR 0002.
3. **Possibly a conveyor-rate counter.** This is needed only if belt footage shows
   that `LineCrossingCounter` cannot separate items that touch.
4. **What a "session" is at NFL.** At Bakers Inn a session is one truck load. At NFL it
   may be a shift, a batch or an order (§9, Q3). The session lifecycle is reused;
   only what opens and closes a session changes.
5. **Number plates probably do not apply.** Nothing in the June specification faces
   a vehicle, so the plate reader (UC-02) is expected to stay off for NFL.

## 8. Use cases

These continue the numbering of `docs/IVaaS_Use_Cases.docx` (UC-01 to UC-23).

| ID | Use case | Primary actor | Reuses |
| --- | --- | --- | --- |
| UC-24 | Count product on the conveyor automatically | Edge AI pipeline | UC-01 flow; `LineCrossingCounter` |
| UC-25 | Count units at the counting point | Edge AI pipeline | UC-01 flow; counter TBC (§5) |
| UC-26 | Count units set down in the picking area | Edge AI pipeline | `StackSettleCounter` / `StackPresenceCounter` |
| UC-27 | Monitor the operator handling area | Edge AI pipeline / Site Manager | TBC (§9, Q1) |
| UC-28 | Reconcile NFL's AI count against its reference count | Site Manager | UC-04; `application/tally.py` |
| UC-29 | Onboard NFL as a tenant with its product type | Platform Admin (Cassava) | UC-20; ADR 0002 |

**UC-24: Count product on the conveyor automatically**
- *Trigger:* a counting session is open for the conveyor point (C4).
- *Main flow:*
  1. The camera streams the belt to the edge node.
  2. The detector finds each item, and the tracker follows it along the belt.
  3. When an item's centre crosses the counting line, one count is posted to the API with its time and camera.
  4. Each count is recorded against the open session and the audit log.
- *Exceptions:*
  - If the camera goes silent, the gap shows as "not measured", never as zero.
  - Items that touch and are counted as one show up when the count is reconciled, not hidden.
- *Postcondition:* the session's AI count is ready to reconcile (UC-28).

**UC-28: Reconcile the AI count against the reference count**
- *Trigger:* the session closes, and the reference count is entered or imported.
- *Main flow:* as in UC-04. The difference is shown, and a Site Manager may approve
  the discrepancy. Approval records the decision and who made it; it never rewrites
  the AI count or improves the accuracy figure.

UC-25 to UC-27 and UC-29 are detailed once the site visit answers §9.

## 9. Open questions for the site visit

| # | Question | Why it matters |
| --- | --- | --- |
| Q1 | What is the unit counted at each point: packet, pack, carton, pallet? Is anything counted at C2? | Chooses the counter, and the labels the detector is trained on |
| Q2 | What is the AI count reconciled against: production records, a manual tally, dispatch notes, an ERP? In what format? | Decides how the reference count gets in (UC-28) |
| Q3 | What is one "session": a shift, a batch, an order? Who opens and closes it, or is it time-based? | Decides how the session lifecycle is driven |
| Q4 | How fast does the belt run, how far apart are items, and do they touch or overlap? | Decides frame rate, tracker settings, and whether a new counter is needed |
| Q5 | What are the mount heights, lighting and glare at each point? | Image quality, and so accuracy |
| Q6 | Where can the edge node sit, and what network reaches each camera? | Deployment |
| Q7 | Which accuracy target and acceptance period will NFL accept? | Success criteria (§6) |
| Q8 | How long are footage and counts kept, and who at NFL may see them? | Retention and roles |
| Q9 | Do any vehicles or plates come into this flow? | Whether the plate reader is used |
| Q10 | Should counts go to an NFL system (webhook or ERP)? | Integration (UC-12) |

## 10. Risks and dependencies

| Risk | Effect | Mitigation |
| --- | --- | --- |
| No footage yet | No detector can be trained, and no accuracy can be measured | Capture footage on the site visit, or as soon as cameras are up |
| Items touch or overlap on the belt | Under-counting | Top-down mount; check spacing on footage (Q4); a dedicated counter if needed |
| Packaging glare under factory lighting | Missed detections | Survey lighting (Q5); camera exposure settings |
| Site visit timing | Delays everything after it | Coordinate early with NFL's site contact |
| No agreed reference count | Accuracy cannot be reported | Agree Q2 before the pilot; until then the report says "not measured" |

## 11. Delivery phases

| Phase | Output | Depends on |
| --- | --- | --- |
| 0. This document | Agreed scope and open questions | Review by Cassava's project sponsor and technical adviser |
| 1. Site survey | Answers to §9; final camera positions | NFL's site contact |
| 2. Install and capture | Cameras streaming; labelled footage from each point | Phase 1; hardware |
| 3. Product type (ADR 0002) | Counts and reports in NFL's units | Phase 1 (Q1) |
| 4. Train and configure | NFL detector; counter chosen and configured for each point | Phase 2 |
| 5. Pilot on one point | Counts reconciled against the reference on one camera (likely C4) | Phases 3 and 4; Q2 |
| 6. Accuracy run | Accuracy over the agreed period | Q7 |
| 7. Rollout | All four points live | Phase 6 accepted |
