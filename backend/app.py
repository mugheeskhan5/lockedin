"""Capture backend; the OS scheduler invokes backend.nightly separately."""
import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from .db import DB_PATH, connect, initialize
from .understanding import queue_job, worker_loop
from .runtime import configure

configure()
from .text_quality import usable_text

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop))
    logger.info("Reels Digest ready; database=%s (timestamps UTC)", DB_PATH)
    try:
        yield
    finally:
        stop.set()
        await worker


app = FastAPI(title="Reels Digest", version="0.5.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.middleware("http")
async def local_ingest_boundary(request: Request, call_next):
    if request.url.path == "/ingest":
        origin = request.headers.get("origin")
        # Worker host permissions allow its POST; grant no website CORS access.
        if origin and not origin.startswith("chrome-extension://"):
            return JSONResponse({"detail": "Unsupported origin"}, status_code=403)
        if request.method == "POST":
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                return JSONResponse({"detail": "application/json required"}, status_code=415)
    return await call_next(request)


class Batch(BaseModel):
    events: list[Any] = Field(min_length=1, max_length=100)


class ReelEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shortcode: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    permalink: str = Field(max_length=256)
    caption: str = Field(default="", max_length=10000)
    captured_at: AwareDatetime = Field(alias="capturedAt")
    creator: str | None = Field(default=None, max_length=256)
    thumbnail_url: str | None = Field(default=None, max_length=4096)
    # Optional understanding input is validated separately; bad frame bytes
    # must never invalidate an otherwise useful caption capture.
    frame_data_url: Any = None
    frame_status: Any = "absent"

    @model_validator(mode="after")
    def matching_permalink(self):
        if self.permalink != f"https://www.instagram.com/reel/{self.shortcode}/":
            raise ValueError("Permalink does not match shortcode")
        self.caption = usable_text(self.caption)
        if self.thumbnail_url:
            parsed = urlparse(self.thumbnail_url)
            host = parsed.hostname or ""
            if parsed.scheme != "https" or parsed.username or parsed.password or not any(
                host == domain or host.endswith("." + domain)
                for domain in ("cdninstagram.com", "fbcdn.net", "instagram.com")
            ):
                self.thumbnail_url = None
        return self


UPSERT = """
INSERT INTO reels (shortcode, permalink, creator, caption, thumbnail_url, captured_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(shortcode) DO UPDATE SET
    caption = CASE WHEN length(excluded.caption) > length(COALESCE(reels.caption, ''))
                   THEN excluded.caption ELSE reels.caption END,
    creator = COALESCE(reels.creator, excluded.creator),
    thumbnail_url = COALESCE(excluded.thumbnail_url, reels.thumbnail_url),
    captured_at = MIN(reels.captured_at, excluded.captured_at)
"""


@app.get("/health")
def health():
    try:
        with closing(connect()) as connection:
            count = connection.execute("SELECT COUNT(*) FROM reels").fetchone()[0]
    except sqlite3.Error:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
            
    try:
        with closing(connect()) as connection:
            jobs = dict(connection.execute(
                "SELECT status, COUNT(*) FROM understanding_jobs GROUP BY status"
            ).fetchall())
            embedded = connection.execute(
                "SELECT COUNT(*) FROM reels WHERE embedding IS NOT NULL"
            ).fetchone()[0]
    except sqlite3.Error:
        jobs, embedded = {}, None
    return {"status": "ok", "reels": count, "embedded": embedded, "understanding": jobs}


@app.post("/ingest")
def ingest(batch: Batch):
    accepted: list[str] = []
    rejected: list[dict] = []
    inserted = 0
    deduped = 0
    valid: list[tuple[int, ReelEvent]] = []
    # Validate independently: one bad event must not discard other rows.
    for index, raw in enumerate(batch.events):
        try:
            valid.append((index, ReelEvent.model_validate(raw)))
        except ValidationError as error:
            reason = "; ".join(
                f"{'.'.join(map(str, item['loc'])) or 'event'}: {item['type']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            rejected.append({"index": index, "error": reason})
            logger.warning("ingest skipped index=%s reason=%s", index, reason)

    try:
        with closing(connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for index, event in valid:
                connection.execute("SAVEPOINT ingest_row")
                try:
                    previous = connection.execute(
                        "SELECT id, caption FROM reels WHERE shortcode = ?", (event.shortcode,)
                    ).fetchone()
                    exists = previous is not None
                    timestamp = event.captured_at.astimezone(timezone.utc).isoformat(
                        sep=" ", timespec="milliseconds"
                    ).removesuffix("+00:00")
                    connection.execute(UPSERT, (
                        event.shortcode, event.permalink, event.creator,
                        event.caption, event.thumbnail_url, timestamp,
                    ))
                    row = connection.execute(
                        "SELECT id, caption FROM reels WHERE shortcode = ?", (event.shortcode,)
                    ).fetchone()
                    queue_job(connection, row["id"], event.frame_data_url, event.frame_status,
                              caption_changed=bool(previous and previous["caption"] != row["caption"]))
                except sqlite3.IntegrityError:
                    connection.execute("ROLLBACK TO ingest_row")
                    rejected.append({"index": index, "error": "database constraint"})
                    logger.warning("ingest skipped index=%s reason=database constraint", index)
                else:
                    accepted.append(event.shortcode)
                    if exists:
                        deduped += 1
                    else:
                        inserted += 1
                finally:
                    connection.execute("RELEASE ingest_row")
            # Commit happens before returning the receipt. Lost responses can be
            # retried safely because the shortcode constraint prevents duplicates.
    except sqlite3.Error:
        logger.exception("Ingest database failure; transaction rolled back, retry required")
        raise HTTPException(status_code=503, detail="Database unavailable; retry batch") from None

    logger.info("ingest received=%d inserted=%d deduped=%d rejected=%d",
                len(batch.events), inserted, deduped, len(rejected))
    return {"accepted": accepted, "inserted": inserted, "deduped": deduped, "rejected": rejected}
