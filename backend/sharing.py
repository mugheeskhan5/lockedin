"""Prepare or manually deliver one 3-6-reel digest to one Discord channel or user DM."""
import argparse
import asyncio
import io
import json
import logging
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .runtime import configure

configure()

import numpy as np
import discord

from .clustering import day_bounds, settings
from .db import connect, initialize
from .message_template import custom_message
from .discord_config import load_config
from .discord_delivery import client, destination, send_pick
from .text_quality import evidence_text

logger = logging.getLogger("uvicorn.error")


def acquire(owner):
    with closing(connect()) as connection, connection:
        return connection.execute("""
            INSERT INTO sharing_lock(id,owner,expires_at) VALUES (1,?,?)
            ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
            WHERE sharing_lock.expires_at < ?
        """, (owner, time.time() + 600, time.time())).rowcount == 1


def renew(owner):
    with closing(connect()) as connection, connection:
        if connection.execute("UPDATE sharing_lock SET expires_at=? WHERE id=1 AND owner=?",
                              (time.time() + 600, owner)).rowcount != 1:
            raise RuntimeError("Delivery lock lost; no further messages will be sent")


def vector(blob):
    values = np.frombuffer(blob, dtype="<f4").astype(np.float64)
    norm = float(np.linalg.norm(values))
    if values.shape != (384,) or not np.isfinite(values).all() or norm <= 0:
        raise ValueError("Invalid embedding")
    return values / norm


def select_reels(day, count):
    bounds = day_bounds(day, settings()["timezone"])
    with closing(connect()) as connection:
        rows = connection.execute("""
            SELECT r.* FROM reels r
            WHERE captured_at>=? AND captured_at<? AND shared=0 AND embedding IS NOT NULL
              AND NOT EXISTS(SELECT 1 FROM digest_items d WHERE d.reel_id=r.id)
            ORDER BY captured_at DESC,id DESC
        """, bounds).fetchall()
        history = connection.execute("""
            SELECT r.embedding FROM reels r WHERE r.embedding IS NOT NULL AND
            (r.shared=1 OR EXISTS(SELECT 1 FROM digest_items d WHERE d.reel_id=r.id AND d.status IN ('sending','unknown')))
            ORDER BY r.shared_at DESC,r.id DESC LIMIT 500
        """).fetchall()
    past = []
    for row in history:
        try:
            past.append(vector(row["embedding"]))
        except Exception:
            logger.warning("Novelty history row skipped: invalid embedding")
    candidates = []
    for row in rows:
        try:
            if not evidence_text(row["caption"], row["ocr_text"]):
                continue
            item = dict(row)
            item["vector"] = vector(item.pop("embedding"))
            item["history_similarity"] = max((float(item["vector"] @ v) for v in past), default=0)
            if item["history_similarity"] >= 0.97:
                continue
            candidates.append(item)
        except Exception:
            logger.warning("Selection skipped reel=%s: invalid text/embedding", row["shortcode"])
    chosen, used = [], set()
    while candidates and len(chosen) < count:
        scored = []
        for item in candidates:
            similarity = max((float(item["vector"] @ x["vector"]) for x in chosen), default=0)
            if similarity >= 0.97:
                continue
            fresh_cluster = item["cluster_id"] is not None and item["cluster_id"] not in used
            novelty = 1 - max(item["history_similarity"], similarity)
            scored.append(((fresh_cluster, novelty, item["id"]), item))
        if not scored:
            break
        selected = max(scored, key=lambda pair: pair[0])[1]
        chosen.append(selected)
        if selected["cluster_id"] is not None:
            used.add(selected["cluster_id"])
        candidates = [item for item in candidates if item["id"] != selected["id"]]
    return chosen


def existing(day):
    with closing(connect()) as connection:
        row = connection.execute("SELECT id FROM digests WHERE date=?", (str(day),)).fetchone()
        return row["id"] if row else None


def items_for(digest_id):
    with closing(connect()) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM digest_items WHERE digest_id=? ORDER BY position", (digest_id,))]


def prepare(day, config, owner, new_batch=False):
    digest_id = existing(day)
    if digest_id is not None:
        if not new_batch:
            return digest_id
        previous = items_for(digest_id)
        if not previous or any(item["status"] != "sent" for item in previous):
            logger.info("Existing batch is unfinished; resume it before requesting another batch")
            return digest_id
        for item in previous:
            # This raises on a changed Discord target. Historical Telegram
            # receipts stay historical and are never resent by this option.
            matching_target(item, config)
        logger.info("Additional batch requested for date=%s; prior deliveries retained", day)
    message = custom_message()
    selected = select_reels(day, config["count"])
    if len(selected) < 3:
        logger.info("No digest prepared: only %d eligible, distinct text-bearing reels; need at least 3", len(selected))
        return None
    prepared = []
    for item in selected:
        renew(owner)
        try:
            comment, source = message, "custom"
            with closing(connect()) as connection:
                photo = connection.execute("SELECT image_bytes FROM reel_thumbnails WHERE reel_id=?", (item["id"],)).fetchone()
            photo = photo[0] if photo else None
            if photo:
                try:
                    from PIL import Image
                    with Image.open(io.BytesIO(photo)) as image:
                        if image.format != "JPEG" or image.width * image.height > 720 * 720:
                            raise ValueError("Invalid thumbnail")
                        image.verify()
                except Exception:
                    photo = None
                    logger.warning("reel=%s thumbnail skipped", item["shortcode"])
            payload = {"shortcode": item["shortcode"], "permalink": item["permalink"],
                       "comment": comment, "comment_source": source,
                       "photo_url": item["thumbnail_url"], "cluster_id": item["cluster_id"],
                       "delivery_provider": "discord", "target_kind": config["target_kind"],
                       "delivery_nonce": uuid.uuid4().hex[:24]}
            prepared.append((item, payload, photo))
        except Exception as error:
            logger.warning("Preparation skipped reel=%s reason=%s", item["shortcode"], type(error).__name__)
    if len(prepared) < 3:
        logger.info("No digest prepared: fewer than 3 usable picks after per-row processing")
        return None
    renew(owner)
    with closing(connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = connection.execute("SELECT owner FROM sharing_lock WHERE id=1").fetchone()
        if not lease or lease[0] != owner:
            raise RuntimeError("Delivery lock lost")
        for item, _, _ in prepared:
            if connection.execute("SELECT shared FROM reels WHERE id=?", (item["id"],)).fetchone()[0]:
                raise RuntimeError("Selection changed; rerun preview")
            if connection.execute("SELECT 1 FROM digest_items WHERE reel_id=?", (item["id"],)).fetchone():
                raise RuntimeError("Selection was already reserved")
        content = "\n\n".join(f"- {payload['comment']}\n  {payload['permalink']}" for _, payload, _ in prepared)
        start_position = 1
        if digest_id is None:
            digest_id = connection.execute("INSERT INTO digests(date,content_md) VALUES (?,?)", (str(day), content)).lastrowid
        else:
            # Keep the schema and every receipt. Append only under the same
            # sharing lock and transaction that reserves the new unique reels.
            previous = connection.execute(
                "SELECT status,position FROM digest_items WHERE digest_id=?", (digest_id,)).fetchall()
            if not new_batch or not previous or any(row["status"] != "sent" for row in previous):
                raise RuntimeError("Delivery state changed; no additional batch prepared")
            start_position = max(row["position"] for row in previous) + 1
            connection.execute("""
                UPDATE digests SET content_md=COALESCE(content_md,'') || ?,sent_to_discord=0
                WHERE id=?
            """, ("\n\nAdditional batch:\n" + content, digest_id))
        for position, (item, payload, photo) in enumerate(prepared, start_position):
            connection.execute("""
                INSERT INTO digest_items(digest_id,reel_id,position,discord_target_id,payload_json,photo_blob)
                VALUES (?,?,?,?,?,?)
            """, (digest_id, item["id"], position, config["discord_target_id"], json.dumps(payload, ensure_ascii=False), photo))
    return digest_id



def refresh_pending_messages(digest_id, owner):
    """Update only unsent Discord payload text, preserving all delivery receipts."""
    message = custom_message()
    renew(owner)
    with closing(connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = connection.execute("SELECT owner FROM sharing_lock WHERE id=1").fetchone()
        if not lease or lease[0] != owner:
            raise RuntimeError("Delivery lock lost")
        rows = connection.execute(
            "SELECT id,status,payload_json FROM digest_items WHERE digest_id=? ORDER BY position",
            (digest_id,)).fetchall()
        content = []
        changed = 0
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                if (row["status"] in {"prepared", "failed"} and payload.get("delivery_provider") == "discord" and
                        (payload.get("comment") != message or payload.get("comment_source") != "custom")):
                    payload.update(comment=message, comment_source="custom")
                    connection.execute("UPDATE digest_items SET payload_json=? WHERE id=?",
                                       (json.dumps(payload, ensure_ascii=False), row["id"]))
                    changed += 1
                content.append(f"- {payload['comment']}\n  {payload['permalink']}")
            except (ValueError, TypeError, KeyError, AttributeError):
                logger.warning("Could not refresh message for malformed delivery item=%s", row["id"])
        if changed:
            connection.execute("UPDATE digests SET content_md=? WHERE id=?", ("\n\n".join(content), digest_id))
            logger.info("Applied custom message to %d pending picks; sent/unknown history retained", changed)


def show(digest_id):
    for item in items_for(digest_id):
        payload = json.loads(item["payload_json"])
        photo = bool(item["photo_blob"] or payload.get("photo_url")) and not item["photo_disabled"]
        print(f"\n{item['position']}. {item['status']} | provider={payload.get('delivery_provider', 'unknown')} | {payload.get('target_kind', 'legacy')}_id={item['discord_target_id']} | thumbnail={'yes' if photo else 'link preview only'} | comment={payload['comment_source']}")
        print(payload["comment"])
        print(payload["permalink"])
        if item["last_error"]:
            print("Delivery note:", item["last_error"])


def claim(item_id, owner):
    with closing(connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = connection.execute("SELECT owner FROM sharing_lock WHERE id=1").fetchone()
        if not lease or lease[0] != owner:
            raise RuntimeError("Delivery lock lost")
        return connection.execute("""
            UPDATE digest_items SET status='sending',attempts=attempts+1,last_error=NULL
            WHERE id=? AND status IN ('prepared','failed') AND not_before<=?
        """, (item_id, time.time())).rowcount == 1


def outcome(item, status, reason=None, message_id=None, disable_photo=False, not_before=0):
    with closing(connect()) as connection, connection:
        connection.execute("""
            UPDATE digest_items SET status=?,last_error=?,message_id=COALESCE(?,message_id),
                photo_disabled=MAX(photo_disabled,?),not_before=?,
                sent_at=CASE WHEN ?='sent' THEN CURRENT_TIMESTAMP ELSE sent_at END WHERE id=?
        """, (status, reason, message_id, disable_photo, not_before, status, item["id"]))
        if status == "sent":
            connection.execute("UPDATE reels SET shared=1,shared_at=CURRENT_TIMESTAMP WHERE id=?", (item["reel_id"],))


def bind_legacy_unsent(digest_id, config, owner):
    """Move only a never-attempted preview to the explicitly configured Discord target."""
    renew(owner)
    with closing(connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = connection.execute("SELECT owner FROM sharing_lock WHERE id=1").fetchone()
        if not lease or lease[0] != owner:
            raise RuntimeError("Delivery lock lost")
        rows = connection.execute("""
            SELECT id,payload_json FROM digest_items
            WHERE digest_id=? AND status='prepared' AND attempts=0
        """, (digest_id,)).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                if payload.get("delivery_provider") != "telegram":
                    continue
                payload.update(delivery_provider="discord", target_kind=config["target_kind"],
                               delivery_nonce=uuid.uuid4().hex[:24])
                connection.execute("""
                    UPDATE digest_items SET discord_target_id=?,payload_json=? WHERE id=?
                """, (config["discord_target_id"], json.dumps(payload, ensure_ascii=False), row["id"]))
                logger.info("Moved never-attempted preview item=%s to Discord", row["id"])
            except (ValueError, TypeError, AttributeError):
                logger.warning("Legacy item=%s has invalid payload; left untouched", row["id"])


def matching_target(item, config):
    payload = json.loads(item["payload_json"])
    if payload.get("delivery_provider") != "discord":
        return False
    if (item["discord_target_id"] != config["discord_target_id"] or
            payload.get("target_kind") != config["target_kind"]):
        raise ValueError("Prepared digest belongs to a different Discord target; refusing to retarget it")
    return True


async def deliver(digest_id, config, owner):
    # Refresh old previews as well as new selections before any send intent.
    refresh_pending_messages(digest_id, owner)
    # A stopped process may already have sent an item. Preserve uncertainty.
    with closing(connect()) as connection, connection:
        connection.execute("""
            UPDATE digest_items SET status='unknown',last_error='Interrupted send; do not auto-retry'
            WHERE digest_id=? AND status='sending'
        """, (digest_id,))
    rows = items_for(digest_id)
    pending = [row for row in rows if row["status"] in {"prepared", "failed"}]
    sendable = []
    for item in pending:
        if matching_target(item, config):
            sendable.append(item)
        else:
            logger.warning("Legacy delivery item=%s retained; no cross-platform retry", item["id"])
    if not sendable:
        with closing(connect()) as connection, connection:
            connection.execute("UPDATE digests SET sent_to_discord=? WHERE id=?",
                               (int(bool(rows) and all(row["status"] == "sent" for row in rows)), digest_id))
        logger.info("No messages to send; sent/unknown/attempted legacy items are never re-shared")
        return
    async with client() as bot:
        await asyncio.wait_for(bot.login(config["token"]), timeout=30)
        target, label = await asyncio.wait_for(destination(bot, config, open_dm=True), timeout=30)
        logger.info("Sending prepared digest to %s", label)
        abort_batch = False
        for item in sendable:
            payload = json.loads(item["payload_json"])
            photo = None if item["photo_disabled"] else item["photo_blob"] or payload.get("photo_url")
            # The same nonce is reused by discord.py's HTTP retries and by a
            # confirmed image-rejection fallback. Long-term exclusion is in SQLite.
            nonce = payload["delivery_nonce"]
            for _ in range(2):
                renew(owner)
                if not claim(item["id"], owner):
                    break
                try:
                    message = await send_pick(target, payload, photo, nonce)
                except (discord.Forbidden, discord.NotFound) as error:
                    outcome(item, "failed", type(error).__name__ + ": check channel permissions or DM privacy")
                    abort_batch = True
                    break
                except discord.RateLimited as error:
                    # The library can raise this while exiting a request context,
                    # including after a successful response. Conservatively keep
                    # the item uncertain rather than risk duplicating it.
                    outcome(item, "unknown", "Rate-limit interruption; delivery uncertain, never auto-retry",
                            not_before=time.time() + float(error.retry_after) + 1)
                    abort_batch = True
                    break
                except discord.HTTPException as error:
                    if error.status in {400, 413}:
                        outcome(item, "failed", "Discord rejected image" if photo else "Discord rejected message",
                                disable_photo=bool(photo))
                        if photo:
                            photo = None
                            continue
                    elif error.status == 401:
                        outcome(item, "failed", "Discord rejected bot credentials")
                        abort_batch = True
                    else:
                        outcome(item, "unknown", "Discord HTTP failure; delivery uncertain, never auto-retry")
                        abort_batch = True
                    break
                except Exception:
                    outcome(item, "unknown", "Send failure or timeout; delivery uncertain, never auto-retry")
                    abort_batch = True
                    break
                else:
                    # If the commit fails, durable 'sending' becomes unknown on
                    # the next run instead of being sent a second time.
                    outcome(item, "sent", message_id=message.id)
                    logger.info("Sent reel=%s message_id=%s", payload["shortcode"], message.id)
                    break
            if abort_batch:
                break
            await asyncio.sleep(1.2)  # Discord delivery pacing; no Instagram actions.
    with closing(connect()) as connection, connection:
        incomplete = connection.execute("SELECT COUNT(*) FROM digest_items WHERE digest_id=? AND status!='sent'", (digest_id,)).fetchone()[0]
        connection.execute("UPDATE digests SET sent_to_discord=? WHERE id=?", (int(incomplete == 0), digest_id))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="today", help="today, yesterday, or YYYY-MM-DD")
    parser.add_argument("--new-batch", action="store_true",
                        help="Explicitly add 3-6 unshared picks after this date's previous batch is fully sent")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Prepare and print; sends nothing (default)")
    mode.add_argument("--send", action="store_true", help="Send this day's prepared picks, preparing first if needed")
    mode.add_argument("--status", action="store_true", help="Only print saved delivery state")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("discord").setLevel(logging.WARNING)
    owner, locked = uuid.uuid4().hex, False
    try:
        initialize(recover_jobs=False)
        config = load_config()
        today = datetime.now(ZoneInfo(settings()["timezone"])).date()
        day = today if args.date == "today" else today - timedelta(days=1) if args.date == "yesterday" else date.fromisoformat(args.date)
        if day > today:
            raise ValueError("Cannot share a future day")
        if args.status and args.new_batch:
            raise ValueError("Use --new-batch with --preview or --send, not --status")
        if args.status:
            digest_id = existing(day)
            if digest_id:
                show(digest_id)
            else:
                print("No digest prepared for this date.")
            return 0
        locked = acquire(owner)
        if not locked:
            print("Another sharing job is running; nothing sent by this command.")
            return 1
        digest_id = prepare(day, config, owner, new_batch=args.new_batch)
        if digest_id is None:
            return 1
        bind_legacy_unsent(digest_id, config, owner)
        refresh_pending_messages(digest_id, owner)
        for item in items_for(digest_id):
            if item["status"] in {"prepared", "failed"}:
                matching_target(item, config)
        if args.send:
            asyncio.run(deliver(digest_id, config, owner))
        show(digest_id)
        if not args.send:
            print("\nPrepared preview only; nothing sent.")
        return 1 if args.send and any(item["status"] != "sent" for item in items_for(digest_id)) else 0
    except Exception as error:
        # Avoid traceback/URL logging that could reveal the Discord token.
        print(str(error) if isinstance(error, ValueError) else f"Sharing stopped ({type(error).__name__}); saved state retained. Use --status.")
        return 1
    finally:
        if locked:
            try:
                with closing(connect()) as connection, connection:
                    connection.execute("DELETE FROM sharing_lock WHERE id=1 AND owner=?", (owner,))
            except Exception:
                logger.warning("Sharing lock cleanup deferred until expiry")


if __name__ == "__main__":
    raise SystemExit(main())
