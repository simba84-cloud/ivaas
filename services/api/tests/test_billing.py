"""M7: golden invoices, proration, entitlements and idempotent metering.

Every expected figure here is worked by hand in the comment beside it, from the
placeholder price book: platform 250.00, counting channel 45.00, plate-reading channel
60.00, API 50.00 a month; storage 0.05 a GB-month and assistant tokens 0.02 a thousand
above the allowances (100 GB-month, 200,000 tokens); VAT 15%.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from ivaas.domain.billing import (
    LPR,
    OD,
    BillingError,
    LimitReached,
    PriceBook,
    Segment,
    Subscription,
    UsageEvent,
    check_channel,
    entitlements,
    month,
    rate,
)

BOOK = PriceBook.load(Path(__file__).parents[1] / "src/ivaas/config/price_book.placeholder.json")
OCT = month("2026-10")  # 31 days
D = Decimal


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def standard(od: int = 16, lpr: int = 1) -> Subscription:
    sub = Subscription(uuid4())
    sub.change(Segment(datetime(2026, 9, 1, tzinfo=UTC), "standard", {OD: od, LPR: lpr}))
    return sub


def amounts(inv) -> dict[str, list[Decimal]]:
    out: dict[str, list[Decimal]] = {}
    for line in inv.lines:
        out.setdefault(line.sku, []).append(line.amount)
    return out


def test_t7_1_a_month_of_standard_with_16_od_and_1_lpr_and_some_overage():
    inv = rate(BOOK, standard(), *OCT, {"storage_gb_month": D(120), "assistant_tokens": D(250_000)})
    assert amounts(inv) == {
        "ivaas-platform": [D("250.00")],
        OD: [D("720.00")],  # 16 x 45.00
        LPR: [D("60.00")],
        "ivaas-storage-overage": [D("1.00")],  # (120 - 100) x 0.05
        "ivaas-assistant": [D("1.00")],  # (250,000 - 200,000) / 1,000 x 0.02
    }
    assert inv.subtotal == D("1032.00")
    assert inv.tax == D("154.80")  # 15% of 1,032.00
    assert inv.total == D("1186.80")
    assert inv.placeholder and inv.price_book == "2026-10-placeholder"


def test_t7_2_upgrading_from_16_to_24_channels_on_the_11th_is_prorated_by_day():
    sub = standard()
    sub.change(Segment(at(11), "standard", {OD: 24, LPR: 1}, by="owner"))
    inv = rate(BOOK, sub, *OCT, {})
    got = amounts(inv)
    # unchanged SKUs stay whole: one line each
    assert got["ivaas-platform"] == [D("250.00")] and got[LPR] == [D("60.00")]
    # 1-10 Oct at 16: 16 x 45 x 10/31 = 7,200/31 = 232.258... -> 232.26
    # 11-31 Oct at 24: 24 x 45 x 21/31 = 22,680/31 = 731.612... -> 731.61
    assert got[OD] == [D("232.26"), D("731.61")]
    assert inv.subtotal == D("1273.87")  # 250.00 + 60.00 + 232.26 + 731.61
    assert inv.tax == D("191.08")  # 15% of 1,273.87 = 191.0805
    assert inv.total == D("1464.95")


def test_a_poc_trial_is_billed_for_its_fourteen_days_only():
    sub = Subscription(uuid4())
    sub.change(Segment(at(5), "poc-trial", {}))
    inv = rate(BOOK, sub, *OCT, {})
    # 5-18 Oct: 14 of 31 days of platform 250, 16 x 45, 60 and API 50 = 1,080 a month
    # 1,080 x 14/31, line by line: 112.90 + 325.16 + 27.10 + 22.58
    assert [line.amount for line in inv.lines] == [D("112.90"), D("325.16"), D("27.10"), D("22.58")]
    assert inv.subtotal == D("487.74")


def test_t7_3_a_17th_counting_channel_on_a_16_channel_plan_is_refused_with_an_upgrade():
    ents = entitlements(BOOK, standard(), at(15))
    check_channel(ents, "od", in_use=15)  # the 16th is fine
    with pytest.raises(LimitReached, match="16 counting channel.*Upgrade"):
        check_channel(ents, "od", in_use=16)
    with pytest.raises(LimitReached, match="1 plate-reading"):
        check_channel(ents, "lpr", in_use=1)
    # no subscription: nothing is limited, and nothing will be billed
    assert entitlements(BOOK, Subscription(uuid4()), at(15)) is None
    check_channel(None, "od", in_use=500)


def test_the_trial_says_when_it_ends_and_limits_come_from_the_quantities_bought():
    sub = Subscription(uuid4())
    sub.change(Segment(at(5), "poc-trial", {}))
    ents = entitlements(BOOK, sub, at(6))
    assert ents.valid_until == at(19) and ents.limits["od_channels"] == 16
    assert ents.features["api"] and not ents.features["sso"]


def test_two_changes_in_the_same_instant_the_later_wins():
    sub = standard()
    sub.change(Segment(at(11), "standard", {OD: 20}))
    sub.change(Segment(at(11), "standard", {OD: 24}))
    assert sub.current(at(11)).quantities[OD] == 24
    assert entitlements(BOOK, sub, at(12)).limits["od_channels"] == 24


def test_changes_go_forward_and_bad_books_and_events_are_refused():
    sub = standard()
    sub.change(Segment(at(11), "standard", {OD: 24}))
    with pytest.raises(BillingError, match="never before"):
        sub.change(Segment(at(10), "standard", {OD: 20}))
    with pytest.raises(BillingError, match="no price"):
        PriceBook.of({**_raw(), "plans": {"x": {"name": "X", "recurring": {"nope": 1}}}})
    with pytest.raises(BillingError, match="no meter"):
        UsageEvent("crates", D(1), at(1), "k")
    with pytest.raises(BillingError, match="idempotency key"):
        UsageEvent("assistant_tokens", D(1), at(1), " ")


def _raw():
    import json

    return json.loads(
        (Path(__file__).parents[1] / "src/ivaas/config/price_book.placeholder.json").read_text()
    )


# --- second slice: wholesale (T7.9) and the billing state (T7.6, T7.8, T7.10) -----------
def test_t7_9_litzims_wholesale_october_for_bakers_inn_and_a_second_customer():
    from ivaas.domain.billing import wholesale

    bakers = Subscription(uuid4())
    bakers.change(Segment(at(5), "poc-trial", {}))  # 5-18 Oct
    acme = standard()  # the whole month
    inv = wholesale(
        BOOK,
        uuid4(),
        "litzim",
        "LITZIM",
        [("Bakers Inn", bakers, {}), ("Acme Foods", acme, {})],
        *OCT,
    )
    # LITZIM's prices, 30% off: platform 175, counting channel 31.50, plates 42, API 35
    by = {p.tenant_name: p for p in inv.parts}
    # Bakers Inn, 14 of 31 days: 175 x 14/31 = 79.03; 16 x 31.50 x 14/31 = 227.61;
    # 42 x 14/31 = 18.97; 35 x 14/31 = 15.81
    assert [x.amount for x in by["Bakers Inn"].lines] == [
        D("79.03"),
        D("227.61"),
        D("18.97"),
        D("15.81"),
    ]
    assert by["Bakers Inn"].subtotal == D("341.42")
    # Acme, all month: 175 + 16 x 31.50 + 42 = 721.00
    assert by["Acme Foods"].subtotal == D("721.00")
    assert inv.subtotal == D("1062.42")
    assert inv.tax == D("159.36")  # 15% of 1,062.42 = 159.363
    assert inv.total == D("1221.78")
    assert inv.price_book == "2026-10-placeholder+wholesale-litzim" and inv.placeholder


def test_wholesale_keeps_unit_prices_precise():
    # 0.02 less 30% is 0.014: rounding it to 0.01 would be a 50% discount, not 30%
    assert BOOK.for_partner("litzim").prices["ivaas-assistant"] == D("0.014")
    with pytest.raises(BillingError, match="no wholesale terms"):
        BOOK.for_partner("nobody")


def _status(**over):
    from ivaas.domain.billing import billing_status

    args = dict(
        current="active",
        on_trial=False,
        subscribed=True,
        overdue_since=[],
        on_hold=False,
        today=datetime(2026, 11, 20).date(),
        grace_days=7,
    )
    return billing_status(**{**args, **over})


def test_t7_6_unpaid_goes_past_due_then_suspended_and_payment_brings_it_back():
    due = datetime(2026, 11, 16).date()
    assert _status(overdue_since=[due], today=due) == "active"  # due today: not late
    assert _status(overdue_since=[due], today=datetime(2026, 11, 17).date()) == "past_due"
    assert _status(overdue_since=[due], today=datetime(2026, 11, 23).date()) == "past_due"
    assert _status(overdue_since=[due], today=datetime(2026, 11, 24).date()) == "suspended"
    assert _status(current="suspended", overdue_since=[]) == "active"  # paid


def test_t7_8_a_trial_on_a_paid_plan_is_active_and_unbilled_states_stay_put():
    assert _status(current="trial", on_trial=True) == "trial"
    assert _status(current="trial", on_trial=False) == "active"  # converted to paid
    for fixed in ("provisioning", "expired", "cancelled"):
        assert _status(current=fixed, on_hold=True) == fixed
    assert _status(current="trial", subscribed=False) == "trial"


def test_t7_10_a_partner_hold_suspends_and_only_lifting_it_returns_the_tenant():
    assert _status(on_hold=True) == "suspended"
    assert _status(current="suspended", on_hold=True, overdue_since=[]) == "suspended"
    assert _status(current="suspended", on_hold=False) == "active"


def test_an_invoice_is_paid_in_full_once_and_only_when_issued():
    from ivaas.domain.billing import Payment

    inv = rate(BOOK, standard(), *OCT, {})
    pay = Payment(D("100.00"), "BT-1", at(31), "finance")
    with pytest.raises(BillingError, match="issue it first"):
        inv.pay(pay)
    inv.number = "IVAAS-2026-000001"
    inv.pay(pay)
    assert not inv.settled and inv.paid == D("100.00")
    inv.pay(Payment(inv.total - D("100.00"), "BT-2", at(31), "finance"))
    assert inv.settled
    with pytest.raises(BillingError, match="already paid"):
        inv.pay(pay)
