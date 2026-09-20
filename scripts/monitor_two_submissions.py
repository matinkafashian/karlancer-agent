"""Read-only bounded monitor. No browser actions or model calls."""

import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import text

from karlancer_agent.config import Settings
from karlancer_agent.db.session import create_engine


async def main():
    engine = create_engine(Settings())
    start = datetime.now(UTC)
    last = None
    try:
        for _ in range(60):
            async with engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            text("""SELECT p.title, s.status, s.created_at,
                    s.submitted_at, s.evidence_hash FROM submissions s JOIN projects p
                    ON p.id=s.project_id WHERE s.created_at >= :start ORDER BY s.created_at"""),
                            {"start": start},
                        )
                    )
                    .mappings()
                    .all()
                )
                circuits = (
                    (
                        await conn.execute(
                            text("SELECT provider,status,retry_at FROM provider_circuits")
                        )
                    )
                    .mappings()
                    .all()
                )
            result = {
                "submissions": [dict(r) for r in rows],
                "circuits": [dict(c) for c in circuits],
            }
            encoded = json.dumps(result, default=str, ensure_ascii=False)
            if encoded != last:
                print(encoded, flush=True)
                last = encoded
            if sum(r["status"] == "submitted" for r in rows) >= 2:
                print("TWO_CONFIRMED", flush=True)
                return
            if any(c["status"] != "closed" for c in circuits):
                print("MONITOR_STOPPED_ON_CIRCUIT", flush=True)
                return
            await asyncio.sleep(30)
        print("MONITOR_TIMEOUT", flush=True)
    finally:
        await engine.dispose()


asyncio.run(main())
