"""Per-row OCR and embeddings. Failures are recorded; later jobs keep running."""
import asyncio
import base64
import io
import logging
import os
import shutil
import time
from contextlib import closing
from pathlib import Path

from .db import BASE_DIR, connect
from .text_quality import evidence_text, usable_text
from .process_lock import process_lock

logger = logging.getLogger("uvicorn.error")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_CACHE = BASE_DIR / "data" / "model-cache"
_model = None
_model_retry_after = 0.0


def decode_frame(data, status):
    allowed = {"ok", "absent", "not-ready", "empty", "tainted", "too-large",
               "unavailable", "error", "storage-full"}
    status = status if isinstance(status, str) and status in allowed else "absent"
    if data is None:
        return None, "absent" if status == "ok" else status
    try:
        if not isinstance(data, str) or len(data) > 180000:
            raise ValueError("frame size/type")
        if not data.startswith("data:image/jpeg;base64,"):
            raise ValueError("frame format")
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        if not raw or len(raw) > 135000:
            raise ValueError("frame size")
        return raw, "ok"
    except Exception as error:
        logger.warning("OCR frame skipped: %s; caption fallback", type(error).__name__)
        return None, "invalid"


def queue_job(connection, reel_id, data, status, caption_changed=False):
    frame, frame_status = decode_frame(data, status)
    if frame is not None:
        connection.execute("""
            INSERT INTO reel_thumbnails(reel_id,image_bytes) VALUES (?,?)
            ON CONFLICT(reel_id) DO UPDATE SET image_bytes=excluded.image_bytes
        """, (reel_id, frame))
    job = connection.execute(
        "SELECT * FROM understanding_jobs WHERE reel_id=?", (reel_id,)
    ).fetchone()
    if job is None:
        connection.execute(
            "INSERT INTO understanding_jobs(reel_id,frame,frame_status) VALUES (?,?,?)",
            (reel_id, frame, frame_status),
        )
        return
    # Caption enrichment invalidates an earlier embedding. A newly readable
    # frame can improve caption-only processing; identical retries are inert.
    better_frame = frame is not None and job["ocr_status"] != "ok" and frame != job["frame"]
    if not caption_changed and not better_frame:
        return
    connection.execute("""
        UPDATE understanding_jobs SET revision=revision+1,status='pending',
            frame=CASE WHEN ? THEN ? ELSE frame END,
            frame_status=CASE WHEN ? THEN ? ELSE frame_status END,
            last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE reel_id=?
    """, (better_frame, frame, better_frame, frame_status, reel_id))
    connection.execute("UPDATE reels SET embedding=NULL WHERE id=?", (reel_id,))


def claim_job(bounds=None, unshared_only=False):
    with closing(connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        where, params = "j.status='pending'", []
        if bounds:
            where += " AND r.captured_at>=? AND r.captured_at<?"
            params.extend(bounds)
        if unshared_only:
            where += " AND r.shared=0"
        row = connection.execute("""
            SELECT j.*,r.shortcode,r.caption,r.ocr_text FROM understanding_jobs j
            JOIN reels r ON r.id=j.reel_id WHERE """ + where + " ORDER BY j.reel_id LIMIT 1",
            params).fetchone()
        if row is None:
            return None
        connection.execute("""
            UPDATE understanding_jobs SET status='processing',attempts=attempts+1,
                updated_at=CURRENT_TIMESTAMP WHERE reel_id=?
        """, (row["reel_id"],))
        return dict(row)


def read_ocr(job):
    if not job["frame"]:
        if usable_text(job["ocr_text"]):
            return usable_text(job["ocr_text"]), "ok", None
        reason = job["frame_status"]
        logger.info("understand reel=%s OCR skipped=%s; caption fallback", job["shortcode"], reason)
        return "", reason, None
    try:
        from PIL import Image, ImageOps, ImageStat
        import pytesseract
        configured = os.environ.get("TESSERACT_CMD")
        default_windows = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        executable = configured or shutil.which("tesseract")
        if not executable and default_windows.is_file():
            executable = str(default_windows)
        if not executable:
            return job["ocr_text"] or "", "unavailable", "Tesseract executable not found"
        pytesseract.pytesseract.tesseract_cmd = executable
        with Image.open(io.BytesIO(job["frame"])) as source:
            if source.format != "JPEG" or source.width * source.height > 720 * 720:
                return job["ocr_text"] or "", "invalid", "Invalid frame dimensions/format"
            gray = source.convert("L")
            if ImageStat.Stat(gray).stddev[0] < 2:
                return job["ocr_text"] or "", "empty", None
            gray = ImageOps.autocontrast(gray)
            text = pytesseract.image_to_string(
                gray, lang=os.environ.get("OCR_LANG", "eng"), config="--psm 11", timeout=8
            )
        text = usable_text(text)[:10000]
        return text or usable_text(job["ocr_text"]), "ok" if text else "empty", None
    except Exception as error:
        # Missing language data, corrupt image, OCR timeout, or missing wrapper
        # all leave caption processing available.
        return job["ocr_text"] or "", "error", f"OCR {type(error).__name__}: {str(error)[:180]}"


def get_model():
    global _model, _model_retry_after
    if _model is not None:
        return _model
    if time.monotonic() < _model_retry_after:
        raise RuntimeError("Embedding model unavailable; run backend.prepare_model and retry failed jobs")
    try:
        import torch
        torch.set_num_threads(int(os.environ.get("DIGEST_CPU_THREADS", "2")))
        from sentence_transformers import SentenceTransformer
        # User downloads weights once with prepare_model; processing is local.
        _model = SentenceTransformer(MODEL_NAME, device="cpu", cache_folder=str(MODEL_CACHE),
                                     local_files_only=True, trust_remote_code=False)
        logger.info("Embedding model loaded locally: %s", MODEL_NAME)
        return _model
    except Exception:
        _model_retry_after = time.monotonic() + 60
        raise


def embed_text(text):
    text = usable_text(text)
    if not text:
        raise ValueError("No lexical text to embed")
    import numpy as np
    model = get_model()
    # Short chunks keep later OCR text from disappearing behind a long caption.
    words = text.split()
    chunks = [" ".join(words[i:i + 100]) for i in range(0, len(words), 100)]
    vectors = model.encode(chunks, batch_size=8, convert_to_numpy=True,
                           normalize_embeddings=True, show_progress_bar=False)
    vector = np.asarray(vectors, dtype=np.float32).mean(axis=0)
    norm = float(np.linalg.norm(vector))
    if vector.shape != (384,) or not np.isfinite(vector).all() or norm <= 0:
        raise ValueError("Invalid embedding shape or values")
    # Explicit portable encoding: 384 little-endian float32s, unit length.
    return (vector / norm).astype("<f4").tobytes()


def finish_job(job, ocr_text, ocr_status, embedding, status, error):
    with closing(connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute("""
            UPDATE understanding_jobs SET status=?,ocr_status=?,last_error=?,
                frame=CASE WHEN ? THEN NULL ELSE frame END,updated_at=CURRENT_TIMESTAMP
            WHERE reel_id=? AND revision=? AND status='processing'
        """, (status, ocr_status, error, ocr_status not in {"error", "unavailable"},
              job["reel_id"], job["revision"])).rowcount
        if changed:
            connection.execute("UPDATE reels SET ocr_text=?,embedding=? WHERE id=?",
                               (ocr_text, embedding, job["reel_id"]))
        else:
            logger.info("understand reel=%s superseded; newer job retained", job["shortcode"])


def process_next(bounds=None, unshared_only=False):
    # Both the live backend and nightly process use this lock. Only its owner
    # may recover 'processing': no other live worker can own such a job now.
    with process_lock("understanding") as acquired:
        if not acquired:
            return False
        with closing(connect()) as connection, connection:
            connection.execute("UPDATE understanding_jobs SET status='pending' WHERE status='processing'")
        return _process_next(bounds, unshared_only)


def _process_next(bounds=None, unshared_only=False):
    job = claim_job(bounds, unshared_only)
    if job is None:
        return False
    ocr_text, ocr_status = job["ocr_text"] or "", "error"
    try:
        ocr_text, ocr_status, error = read_ocr(job)
        if error:
            logger.warning("understand reel=%s %s; caption fallback", job["shortcode"], error)
        ocr_text = usable_text(ocr_text)
        text = evidence_text(job["caption"], ocr_text)
        if not text:
            finish_job(job, ocr_text, ocr_status, None, "skipped", error or "No lexical caption or OCR text")
            logger.info("understand reel=%s skipped=no-text", job["shortcode"])
            return True
        try:
            embedding = embed_text(text)
        except Exception as embedding_error:
            reason = f"Embedding {type(embedding_error).__name__}: {str(embedding_error)[:240]}"
            logger.warning("understand reel=%s skipped=%s", job["shortcode"], reason)
            finish_job(job, ocr_text, ocr_status, None, "error", reason)
            return True
        finish_job(job, ocr_text, ocr_status, embedding, "done", error)
        logger.info("understand reel=%s status=done ocr=%s embedding_bytes=%d",
                    job["shortcode"], ocr_status, len(embedding))
    except Exception as error:
        # Includes unexpected per-row failures: save a reason and move on.
        logger.exception("understand reel=%s row skipped", job["shortcode"])
        try:
            finish_job(job, ocr_text, ocr_status, None, "error", f"{type(error).__name__}: {str(error)[:240]}")
        except Exception:
            logger.exception("Could not persist failed job; it will resume on backend restart")
    return True


async def worker_loop(stop):
    logger.info("Understanding worker started (one local CPU worker)")
    while not stop.is_set():
        try:
            worked = await asyncio.to_thread(process_next)
        except Exception:
            logger.exception("Understanding queue unavailable; will retry")
            worked = False
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
    logger.info("Understanding worker stopped")
