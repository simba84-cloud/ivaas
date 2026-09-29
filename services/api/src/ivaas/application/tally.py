"""Use cases for tally sheets: save a sheet, find its session, reconcile it.

A sheet's figure becomes the session's manual count through the existing
`ReconcileSession`, so accuracy, disputes and sign-off behave exactly as for a count
typed in by hand. A sheet never overwrites a manual count that is already there: a
disagreement is recorded as a conflict for an admin to settle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ivaas.application.sessions import ReconcileSession
from ivaas.domain.models import SessionStatus
from ivaas.domain.tally import TallySheet, TallyStatus, match_session
from ivaas.ports.repositories import BayReader, Clock, SessionReader, SiteReader
from ivaas.ports.tally import TallySheetStore

#: sessions are searched from this long before the sheet's window opens
LOOKBACK = timedelta(days=1)


@dataclass
class _Resolver:
    store: TallySheetStore
    sessions: SessionReader
    bays: BayReader
    sites: SiteReader
    reconcile: ReconcileSession
    clock: Clock

    async def _tz(self, bay_id: UUID) -> ZoneInfo:
        bay = await self.bays.get(bay_id)
        site = await self.sites.get(bay.site_id) if bay else None
        try:
            return ZoneInfo(site.timezone if site else "UTC")
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    async def resolve(self, sheet: TallySheet, taken: set[UUID]) -> TallySheet:
        """Link the sheet to its session and reconcile when the session allows it."""
        session = await self.sessions.get(sheet.session_id) if sheet.session_id else None
        if session is None:
            tz = await self._tz(sheet.bay_id)
            start, _ = sheet.window(tz)
            candidates = await self.sessions.list_recent(
                bay_id=sheet.bay_id, since=start - LOOKBACK, limit=500
            )
            session = match_session(
                sheet, candidates, tz=tz, now=self.clock.now(), taken=taken - {sheet.session_id}
            )
        if session is None:
            sheet.session_id, sheet.status = None, TallyStatus.UNMATCHED
        else:
            sheet.session_id = session.id
            if session.status is SessionStatus.OPEN:
                sheet.status = TallyStatus.MATCHED  # reconciles once the truck has left
            elif session.manual_count is None:
                await self.reconcile(session.id, sheet.truth)
                sheet.status = TallyStatus.RECONCILED
            elif session.manual_count == sheet.truth:
                sheet.status = TallyStatus.RECONCILED
            else:
                sheet.status = TallyStatus.CONFLICT
            taken.add(session.id)
        await self.store.save(sheet)
        return sheet


@dataclass
class SaveTallySheets(_Resolver):
    """Enter one or more sheets. Re-entering a sheet id replaces it.

    A sheet that already reconciled its session keeps that link; if its figure has
    since changed it becomes a conflict rather than silently rewriting the count.
    Any other sheet is matched afresh, since its plate or times may be what changed.
    """

    async def __call__(self, sheets: list[TallySheet]) -> list[TallySheet]:
        taken = await self.store.session_ids_taken()
        saved = []
        for sheet in sheets:
            existing = await self.store.get_by_sheet_id(sheet.sheet_id)
            if existing is not None:
                sheet.id = existing.id
                if (
                    existing.status is TallyStatus.RECONCILED
                    or existing.status is TallyStatus.CONFLICT
                ):
                    sheet.session_id = existing.session_id
                else:
                    taken.discard(existing.session_id)
                    sheet.session_id = None
            saved.append(await self.resolve(sheet, taken))
        return saved


@dataclass
class RematchTallySheets(_Resolver):
    """Retry every sheet that has not reconciled yet: a session may have closed since,
    or a truck's plate may have been assigned by hand."""

    async def __call__(self) -> list[TallySheet]:
        taken = await self.store.session_ids_taken()
        changed = []
        for sheet in await self.store.list_unresolved():
            before = (sheet.status, sheet.session_id)
            await self.resolve(sheet, taken)
            if (sheet.status, sheet.session_id) != before:
                changed.append(sheet)
        return changed
