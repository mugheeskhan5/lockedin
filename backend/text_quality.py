"""Reject text-free evidence without guessing a video's meaning."""
import logging
import unicodedata

logger = logging.getLogger("uvicorn.error")
TEXT_POLICY = "lexical-text-v1"


def usable_text(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value)
    # Replace control/format characters with spaces rather than joining words.
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    value = " ".join(value.split())
    # Unicode letters preserve non-English text. Bullets, punctuation, emoji,
    # whitespace and numeric counters alone are not topical text evidence.
    return value if any(char.isalpha() for char in value) else ""


def evidence_text(caption, ocr_text):
    return "\n".join(part for part in (usable_text(caption), usable_text(ocr_text)) if part)


def repair_existing(connection):
    """One-time, transactional repair of derived data; keep original captures."""
    if connection.execute("SELECT 1 FROM maintenance_versions WHERE name=?", (TEXT_POLICY,)).fetchone():
        return
    affected_clusters = set()
    repaired = 0
    rows = connection.execute("""
        SELECT id,caption,ocr_text,cluster_id FROM reels
        WHERE embedding IS NOT NULL OR cluster_id IS NOT NULL
    """).fetchall()
    for row in rows:
        if evidence_text(row["caption"], row["ocr_text"]):
            continue
        if row["cluster_id"] is not None:
            affected_clusters.add(row["cluster_id"])
        connection.execute("UPDATE reels SET embedding=NULL,cluster_id=NULL WHERE id=?", (row["id"],))
        # A saved but previously failed frame may still yield useful OCR.
        # Revision bumps prevent an in-flight old job from restoring its result.
        connection.execute("""
            INSERT OR IGNORE INTO understanding_jobs(reel_id,status,last_error)
            VALUES (?,'skipped','No lexical caption or OCR text')
        """, (row["id"],))
        connection.execute("""
            UPDATE understanding_jobs SET revision=revision+1,
                status=CASE WHEN frame IS NOT NULL THEN 'pending' ELSE 'skipped' END,
                last_error='No lexical caption or OCR text',updated_at=CURRENT_TIMESTAMP
            WHERE reel_id=?
        """, (row["id"],))
        repaired += 1
    for cluster_id in affected_clusters:
        cluster = connection.execute("SELECT session_date FROM clusters WHERE id=?", (cluster_id,)).fetchone()
        # The old label/summary used invalid evidence, so invalidate the entire
        # affected group, preserving every member reel and its original fields.
        connection.execute("UPDATE reels SET cluster_id=NULL WHERE cluster_id=?", (cluster_id,))
        connection.execute("DELETE FROM clusters WHERE id=?", (cluster_id,))
        if cluster:
            connection.execute("""
                UPDATE cluster_runs SET status='stale',input_hash='',
                    cluster_count=(SELECT COUNT(*) FROM clusters WHERE session_date=?),
                    error='Text-free evidence removed; rerun clustering for this date'
                WHERE session_date=?
            """, (cluster["session_date"], cluster["session_date"]))
    connection.execute("INSERT INTO maintenance_versions(name) VALUES (?)", (TEXT_POLICY,))
    logger.info("Text-evidence repair: invalidated %d embeddings and %d affected clusters; captures preserved",
                repaired, len(affected_clusters))
