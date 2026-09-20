"""One-off reconciliation of the reviewed successful screener submission."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from bs4 import BeautifulSoup
from sqlalchemy import select

from karlancer_agent.config import Settings
from karlancer_agent.db.models import AuditEventRow, ProjectRow, ProviderCircuitRow, SubmissionRow
from karlancer_agent.db.session import create_engine, create_session_factory


async def main():
    evidence = Path(
        "/data/evidence/c54e78fe-5c73-406d-8e12-46c12dffec9b-20260919T233322Z-ambiguous.html"
    )
    soup = BeautifulSoup(evidence.read_text(), "lxml")
    modal = soup.select_one("#setBidModal.show")
    assert modal and "پیشنهاد شما با موفقیت ثبت شد" in modal.get_text(" ", strip=True)
    engine = create_engine(Settings())
    try:
        async with create_session_factory(engine)() as session, session.begin():
            pid = UUID("c54e78fe-5c73-406d-8e12-46c12dffec9b")
            p = await session.get(ProjectRow, pid)
            s = await session.scalar(select(SubmissionRow).where(SubmissionRow.project_id == pid))
            assert p and s and "1n694e03o82o" in p.canonical_project_url
            if s.status == "paused_error":
                assert "ambiguous_after_click" in (p.last_error or "")
                s.status = "submitted"
                s.submitted_at = s.created_at
                p.state = "reply_watch"
                p.last_error = None
                session.add(
                    AuditEventRow(
                        project_id=pid,
                        event_type="submission_reconciled",
                        actor="operator",
                        payload={"evidence": evidence.name, "reason": "visible success dialog"},
                    )
                )
            assert s.status == "submitted"
            guard = await session.get(ProviderCircuitRow, "system")
            last = await session.scalar(
                select(AuditEventRow)
                .where(AuditEventRow.event_type == "system_paused")
                .order_by(AuditEventRow.created_at.desc())
                .limit(1)
            )
            assert last and last.payload.get("reason") == "ambiguous_after_click"
            if guard:
                guard.status = "closed"
                guard.retry_at = None
                guard.consecutive_failures = 0
            print(json.dumps({"reconciled": str(pid), "system_paused": False}))
    finally:
        await engine.dispose()


asyncio.run(main())
