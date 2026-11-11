# Crate control: controlled bays, standard units and a per-driver ledger

| Item | Detail |
| --- | --- |
| Document date | 11 November 2026 |
| Status | **Feasibility note for review.** Phase 1 is being built on `feature/crate-control`. Nothing here is installed at a bay. |
| Client / platform | Bakers Inn / Cassava (IVaaS, Video Analytics as a Service) |
| Audience | Carlos, and Bakers Inn |
| Related | [ADR 0004: controlled bays](../adr/0004-controlled-bays.md); [ARCHITECTURE.md](../ARCHITECTURE.md) §2a''-2a-iii (plates, balances, exceptions); `ml/README.md` (layer counter) |

Every figure here was measured and comes with its sample size. Anything we do not
know is marked **TBC**, and the questions for the client are listed in §5.

## 1. The proposal in one paragraph

Today IVaaS tries to count every crate wherever it is seen, then reconcile that count
against a manual one. The proposal is to control the process instead:
- Drivers check in at **designated dispatch and return bays** with a badge or PIN, and the plate reader confirms the truck.
- Crates are presented in **standard units**, for example 2 columns × 14 crates, or whatever unit the client defines.
- The AI **checks that the standard was met** (a full column of the right height) instead of counting freely.
- Every crate issued and returned is **traced to a driver**, which gives each driver a running balance and an exception list.

The losses stop because someone is accountable for every crate.

## 2. Why checking a standard is easier than counting

The layer counter (layers-v3) answers "how many crates high is this column?". Its
recorded error is an average miss of 1.1 crates, and 69% of its reads are within ±1.
For a free count that is not 95%-grade.

A controlled bay asks two easier questions:
- **"Are there K columns?"** The stack detector already answers this; it boxes one column per stack.
- **"Is each column exactly N high?"** This is a yes/no question about one known height.

The labelled crops suggest a standard column already exists: 125 of the 240 labelled
stacks are 14 high.

## 3. What we measured

`ml/src/ivaas_ml/standard_check.py` runs layers-v3 on the crops it was validated on
and scores the yes/no question "is this column exactly N high?" for each height.
Those crops come from three whole clips that were held out of training (CC 2_13,
CC 3_18 and CC 4_29), 71 crops in all. The script reproduces the model's recorded
figures (average miss 1.10, 69% within ±1), so it is scoring the same split.

```bash
cd ml && uv run --extra train python -m ivaas_ml.standard_check
```

**At the standard height of 14 (35 true 14s, 36 other columns):**

| Measure | Result | 95% interval | What it means at a bay |
| --- | --- | --- | --- |
| A true 14 is passed | 31 of 35 (89%) | | |
| **False reject:** a true 14 is failed | 4 of 35 (11%) | 5%-26% | A good column goes to a supervisor |
| **False accept:** another height is passed as 14 | 2 of 36 (6%) | 2%-18% | A short or over column goes through unnoticed |
| Correct yes/no overall | 65 of 71 (92%) | 83%-96% | |

The two false accepts were a 10 and a 16, both read as 14.

**Other heights.** Every other height has 10 or fewer true examples in the held-out
clips, too few to support a rate. Where there are exactly 10, the model fails most
good columns: 6 of 10 true 8s and 8 of 10 true 10s were not read as their own height.
This matches what `ml/README.md` records: mid-range door stacks scatter (an 8 can read
5 or 12).

**How far these figures can be trusted:**
- **All 35 true 14s come from one clip** (CC 3_18, the night yard). They are not 35
  independent columns. Some are the same stack seen in more than one frame. The real
  evidence is thinner than "35" suggests.
- **The same clips chose the model's best training epoch**, so the figures are, if
  anything, optimistic.
- **The crops are single frames** cut from stacks being dragged across a yard or into a
  truck, not columns standing still in front of a bay camera. At a bay the pipeline takes
  the median over many frames of each column and reports how many frames agree. That
  should help, but it has **not been measured** and is not claimed here.

**What this supports:**
- **Passing a column automatically on the layer counter alone is not good enough yet.**
  Up to 18% of wrong columns could pass at the 14 standard, and that is the error a
  loss-control process cannot afford.
- **Columns that fail should go to a person, not be counted as short.** An 11% false
  reject means some good columns will be flagged. A supervisor must check them against
  the evidence clip. The phase 1 design does this: a column the AI cannot verify is
  shown as **unverified**, never as a number.
- **A real figure needs bay footage.** About 200 labelled column crops from a side-on
  camera at a controlled bay, held out by day, would give a figure the client can
  accept. That needs the bay camera in §4.

## 4. What is doable now, and what needs hardware

**Doable now, in software (phase 1, being built):**

| Capability | Status |
| --- | --- |
| Driver register: name, employee number, badge code, optional PIN (hashed, never shown) | Being built |
| Bays marked "controlled", each with its standard unit (columns × crates per column) | Being built |
| Check-in at a bay: badge or PIN, dispatch or return, number of standard units declared | Being built |
| Each counted column stored with how strongly the frames agreed on its height | Being built (pipeline `agreement` signal) |
| Each transaction checked against the standard: **verified**, **short**, **over**, **non-standard** (right total, wrong columns) or **unverified** | Being built |
| A supervisor can accept an exception with a reason; the counts are never changed | Being built |
| Balances per driver: issued, returned, outstanding, with unverified transactions shown separately | Being built |
| Plate reading, truck register, manifests, exceptions, balances per truck or route | Exists today |
| Every check-in and acceptance recorded in the audit log, with who did it | Exists today (new actions being added) |

**Needs hardware, per controlled bay:**

| Item | Why | Status |
| --- | --- | --- |
| A tablet or kiosk | Where the driver scans a badge or types a PIN, and sees the result | **TBC:** model, mounting, power |
| An LPR camera facing the plate | Ties the transaction to the truck. No camera faces a plate today, which is why plates are missing now | **TBC** |
| A side-on bay camera | Sees the presented columns standing still, top to floor, so each column's height can be checked. The current cameras see stacks in motion from above or behind the truck door | **TBC:** position depends on the bay layout |
| A badge printer or printed QR cards | Driver badges | **TBC** |

The bay camera is the one that matters most for accuracy. The figures in §3 come from
footage it would replace.

## 5. What the client must define

| # | Question | Why it matters |
| --- | --- | --- |
| C1 | **What is the standard unit?** How many columns, and how many crates per column (for example 2 × 14, or 25 or 50 crates)? Is it the same for dispatch and return, and for every product? | The AI checks against it. Without it there is nothing to verify |
| C2 | **Which bays are controlled?** Which dispatch bays and which return bays, and do all loads go through them? | Loads outside a controlled bay are not traced to a driver |
| C3 | **The driver register.** Who are the drivers, what identifies each one (employee number), and who issues and withdraws badges? | Every transaction is booked to a driver |
| C4 | **What happens at a short or over unit?** Is the load stopped, flagged, or charged to the driver? Who decides? | Sets the supervisor's workflow and who may accept an exception |
| C5 | **Are part units allowed** (for example the last unit of a route)? | Decides whether the check is "exactly N" or "at most N" for the last column |
| C6 | **How does a driver's balance close?** Per trip, per day, per week? Against what? | Sets the balance period |

## 6. What needs legal work

**Face recognition of drivers needs a data protection review before it is built.** A
face is biometric data. Zimbabwe's Cyber and Data Protection Act requires a lawful
basis, and POTRAZ is the data protection authority. That review is the client's and
Cassava's to commission; it is not an engineering decision.

The platform is already built for this:
- The security module's face recognition stays off until an administrator records
  its legal basis in Settings.
- Every enrolled person carries a reference to where their consent is kept.
- Only an encrypted face embedding is stored, never the photo.

Phase 2 would reuse that. Phases 1 and 3 process no biometric data. A badge, a PIN
and a number plate are personal data, but they are not biometric.

## 7. Phases

| Phase | What it delivers | Depends on |
| --- | --- | --- |
| **1. Badge + plate, controlled bays** | Driver register and badges. Check-in at the bay. Each unit checked against the standard. Supervisor exceptions. A per-driver crate ledger with balances. | C1-C4 from the client. A kiosk and an LPR camera per bay. A side-on bay camera for the check to be measured, as §3 explains |
| **2. Face match suggests, badge confirms** | At check-in the bay camera suggests which driver is present. The driver still confirms with the badge, so a wrong match cannot book crates to the wrong person. | The DPA review (§6). Each driver's recorded consent. Phase 1 running |
| **3. ERP manifests** | The number of units each driver should take or return comes from the client's dispatch system instead of being declared at the kiosk. | Access to the client's ERP and its export format (**TBC**) |

## 8. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| The layer check passes a wrong column (6% measured, up to 18%) | A loss goes unnoticed | Phase 1 shows unverified columns to a person. Measure again on bay footage before any column passes on the AI alone |
| The layer check fails a good column (11% measured) | Supervisor workload; drivers lose trust | Accept with a reason, without changing the count. Measure on bay footage |
| Loads bypass the controlled bays | Crates not traced to anyone | Client policy (C2). Unmatched plates already show up as exceptions |
| Badge sharing | Crates booked to the wrong driver | The plate reader cross-checks the truck. Phase 2 face match, subject to §6 |
| No standard unit agreed | Nothing to validate | C1 before the pilot |
