"""Phase 3 backend-resident schedule; full cron/systemd pipeline is Phase 5."""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .clustering import run_day, settings

logger = logging.getLogger("uvicorn.error")


async def scheduler_loop(stop):
    if os.environ.get("CLUSTER_SCHEDULE_ENABLED", "1") == "0":
        logger.info("Nightly clustering schedule disabled")
        return
    try:
        config = settings()
        hour, minute = map(int, os.environ.get("CLUSTER_TIME", "00:10").split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("CLUSTER_TIME must be HH:MM")
    except Exception as error:
        logger.error("Clustering schedule skipped: %s; ingest/understanding remain active", error)
        return
    logger.info("Nightly clustering scheduled at %02d:%02d %s for the previous day",
                hour, minute, config["timezone"])
    while not stop.is_set():
        try:
            now = datetime.now(ZoneInfo(config["timezone"]))
            if (now.hour, now.minute) >= (hour, minute):
                await asyncio.to_thread(run_day, now.date() - timedelta(days=1), config, False, True)
            # Repeat checks pick up late embeddings; unchanged input hashes do
            # no further clustering or LLM work. This also catches yesterday up
            # when the backend starts after its scheduled time.
        except Exception:
            logger.exception("Scheduled clustering skipped; next check remains scheduled")
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except asyncio.TimeoutError:
            pass
