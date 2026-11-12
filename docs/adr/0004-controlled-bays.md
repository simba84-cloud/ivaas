# ADR 0004: Controlled bays validate a standard unit instead of counting freely

- **Status:** proposed. Phase 1 is being built on `feature/crate-control`. Validation
  on the AI alone waits for a figure measured on bay footage.
- **Date:** 2026-11-11
- **Source:** [`docs/modules/crate-control.md`](../modules/crate-control.md);
  `ml/src/ivaas_ml/standard_check.py`

## Context

IVaaS counts crates freely. The stack detector boxes one column per stack, and the
layer counter (layers-v3) reads each column's height. Each counted stack is posted as
a crossing `{crates, confidence}`, and the API adds it to the load's `ai_count`. The
total is reconciled against a manual count.

Free counting has three weaknesses:
- **It is not accurate enough on its own.** layers-v3 misses by 1.1 crates on average,
  and 69% of its reads are within ±1 (71 held-out crops).
- **It cannot say when it is unsure.** `OnnxLayerCounter` reports a confidence of 1.0
  every time, so nothing can mark a column as unverified.
- **It does not find who is responsible.** A load is tied to a truck by its plate,
  but not to the driver, so a loss has no owner.

The client's process is already standardised. Of the 240 labelled stacks, 125 are 14
high. The proposal is that drivers check in at designated bays and present crates in a
standard unit (columns × crates per column), and that the AI checks the unit was met.

`standard_check.py` measured how well layers-v3 answers "is this column exactly 14
high?" on its held-out crops:

| Measure | Result | 95% interval |
| --- | --- | --- |
| False reject: a true 14 is failed | 4 of 35 (11%) | 5%-26% |
| False accept: another height is passed as 14 | 2 of 36 (6%) | 2%-18% |

All 35 true 14s come from one clip, and the same clips chose the best training epoch.
Every other height has 10 or fewer examples, too few for a rate.

## Decision

1. **Controlled bays.** A bay can be marked controlled.
   - A controlled bay has a standard unit: `standard_columns × standard_layers` crates.
   - A bay without a standard cannot be controlled.
   - Uncontrolled bays keep counting freely, as they do today.
2. **Validate, do not count.** At a controlled bay a transaction declares a number of
   units. The pure function `validate()` in `domain/crate_control.py` checks the observed
   columns against the standard:
   - A column passes only if its height equals the standard **and** its evidence is
     sound: enough frames, and enough of them agreeing.
   - Any column with unsound evidence, or no columns seen at all, makes the transaction
     **UNVERIFIED**. It is never turned into "0" or a guess.
   - Otherwise the crates counted are compared with `declared_units × standard_columns
     × standard_layers`, giving **SHORT** or **OVER**. If the total matches but a column
     is not standard (a 13 and a 15 instead of two 14s), the result is
     **NON_STANDARD**. Only all-standard columns, in the expected number, are
     **VERIFIED**.
3. **The pipeline reports agreement.** Each stack counter already takes the median of
   its per-frame estimates. It now also reports `agreement`, the share of frame
   estimates equal to that median. This becomes the "can this column be verified"
   signal and replaces the meaningless confidence of 1.0. The field is optional in the
   ingest payload, so older edge nodes keep working.
4. **Every transaction has a driver.** A driver checks in with a badge or a PIN, and
   the plate reader confirms the truck.
   - Balances are kept per driver: issued, returned and outstanding.
   - Unverified transactions are listed separately and never folded into the totals.
   - The PIN is hashed with Argon2id and never returned.
5. **People accept exceptions; they do not edit counts.** A supervisor may accept a
   SHORT, OVER, NON_STANDARD or UNVERIFIED transaction with a reason. The acceptance is audited. The
   observed counts and the accuracy figures do not change.
6. **Face recognition is not part of this decision.** A later phase may let a face
   match *suggest* the driver, with the badge confirming. It reuses the security face
   stack (`EnrolledPerson`, `opencv_faces`), stays behind `FACE_RECOGNITION_BASIS`, and
   waits for a data protection (DPA / POTRAZ) review.

## Consequences

- **Nothing passes on the AI alone yet.** At the measured false accept rate (up to 18%),
  a column that fails or cannot be verified goes to a supervisor. Changing that needs a
  figure measured on footage from a side-on bay camera, held out by day.
- **Hardware per controlled bay.** Each bay needs a kiosk or tablet, an LPR camera
  facing the plate, and a side-on camera. None is installed.
- **Client decisions first.** The standard unit, the controlled bays and the driver
  register are the client's to define (`docs/modules/crate-control.md` §5). Without a
  standard there is nothing to validate.
- **Tenant data.** `drivers`, `crate_transactions` and `stack_observations` carry
  `tenant_id`, with `ENABLE` + `FORCE ROW LEVEL SECURITY` and the `tenant_isolation`
  policy, or `tests/test_rls.py` fails. The secrets no-leak test covers `pin_hash`.
- **Single columns are stored.** Until now the API only added to `ai_count`. At a
  controlled bay each column is kept as a `StackObservation`, so a disputed
  transaction can be checked column by column against its evidence clip.
- **Free counting stays.** Uncontrolled bays, uploaded-video analysis and the POC
  report work as before. This is an extra mode, not a replacement.
