"""Filed reports on Postgres keep their workbook's key, and older ones read back
without one (migration 0027)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ivaas.adapters.persistence.postgres import build_postgres_repositories
from ivaas.adapters.persistence.reports_postgres import PostgresReportStore
from ivaas.adapters.persistence.secrets import SecretBox
from ivaas.application.reports import StoredReport
from ivaas.config.container import demo_topology
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.tenancy import tenant_context

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_the_workbook_key_is_kept_and_older_reports_have_none(postgres_url):
    site, bay, cams = demo_topology()
    *_, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(site, bay, cams), box=SecretBox([SecretBox.generate_key()])
    )
    store = PostgresReportStore(sm)
    at = datetime(2026, 10, 2, 4, 30, tzinfo=UTC)
    try:
        with tenant_context(BAKERS_INN_ID):
            await store.save(
                StoredReport(
                    site.id, date(2026, 10, 1), "r/1.pdf", "r/1.csv", 5, at, xlsx_key="r/1.xlsx"
                )
            )
            await store.save(StoredReport(site.id, date(2026, 9, 30), "r/0.pdf", "r/0.csv", 3, at))
            new, old = await store.since(date(2026, 9, 1))
        assert (new.day, new.xlsx_key) == (date(2026, 10, 1), "r/1.xlsx")
        assert (old.day, old.xlsx_key) == (date(2026, 9, 30), None)
    finally:
        await dispose()
