"""Daily HDBSCAN job. CLI and backend scheduler use the same implementation."""
import argparse
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import closing
from datetime import date, datetime, time as daytime, timedelta, timezone
from zoneinfo import ZoneInfo

from .runtime import configure

configure()

from .cluster_labels import label_cluster
from .db import connect, initialize
from .text_quality import TEXT_POLICY, evidence_text, usable_text

logger = logging.getLogger("uvicorn.error")


def settings():
    zone = os.environ.get("DIGEST_TIMEZONE", "Asia/Karachi")
    ZoneInfo(zone)
    minimum = int(os.environ.get("CLUSTER_MIN_SIZE", "3"))
    samples = int(os.environ.get("CLUSTER_MIN_SAMPLES", "2"))
    if minimum < 2 or samples < 1:
        raise ValueError("CLUSTER_MIN_SIZE >= 2 and CLUSTER_MIN_SAMPLES >= 1 required")
    return {"timezone": zone, "min_size": minimum, "min_samples": samples,
            "text_policy": TEXT_POLICY,
            "model": os.environ.get("OLLAMA_MODEL", "llama3.2:1b"),
            "endpoint": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")}


def day_bounds(day, zone):
    tz = ZoneInfo(zone)
    def stamp(value):
        return datetime.combine(value, daytime.min, tz).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return stamp(day), stamp(day + timedelta(days=1))


def snapshot(connection, bounds):
    return [dict(row) for row in connection.execute("""
        SELECT id,shortcode,caption,ocr_text,embedding FROM reels
        WHERE captured_at>=? AND captured_at<? ORDER BY id
    """, bounds)]


def fingerprint(rows, config):
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    for row in rows:
        digest.update(json.dumps([row["id"], row["shortcode"], row["caption"], row["ocr_text"]],
                                 ensure_ascii=True).encode())
        raw = row["embedding"]
        payload = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else repr(raw).encode()
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def lock_day(day, owner):
    with closing(connect()) as connection, connection:
        return connection.execute("""
            INSERT INTO cluster_locks(session_date,owner,expires_at) VALUES (?,?,?)
            ON CONFLICT(session_date) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
            WHERE cluster_locks.expires_at < ?
        """, (str(day), owner, time.time() + 600, time.time())).rowcount == 1


def renew_lock(day, owner):
    with closing(connect()) as connection, connection:
        if connection.execute("UPDATE cluster_locks SET expires_at=? WHERE session_date=? AND owner=?",
                              (time.time() + 600, str(day), owner)).rowcount != 1:
            raise RuntimeError("Clustering lease lost; rerun this date")


def record_run(connection, day, config, signature, status, counts, error=None):
    connection.execute("""
        INSERT INTO cluster_runs(session_date,timezone,input_hash,status,model,cluster_count,
            noise_count,skipped_count,llm_failures,updated_at,error)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(session_date) DO UPDATE SET timezone=excluded.timezone,input_hash=excluded.input_hash,
            status=excluded.status,model=excluded.model,cluster_count=excluded.cluster_count,
            noise_count=excluded.noise_count,skipped_count=excluded.skipped_count,
            llm_failures=excluded.llm_failures,updated_at=excluded.updated_at,error=excluded.error,
            attempts=cluster_runs.attempts+1
    """, (str(day), config["timezone"], signature, status, config["model"],
          counts["clusters"], counts["noise"], counts["skipped"], counts["llm_failures"], time.time(), error))


def run_day(day, config=None, force=False, scheduled=False):
    config = config or settings()
    owner = uuid.uuid4().hex
    counts = {"clusters": 0, "noise": 0, "skipped": 0, "llm_failures": 0}
    signature = ""
    if not lock_day(day, owner):
        logger.info("cluster date=%s already running; skipped overlapping job", day)
        return "busy"
    try:
        bounds = day_bounds(day, config["timezone"])
        with closing(connect()) as connection:
            rows = snapshot(connection, bounds)
            previous = connection.execute("SELECT * FROM cluster_runs WHERE session_date=?", (str(day),)).fetchone()
        signature = fingerprint(rows, config)
        if previous and not force:
            if previous["input_hash"] == signature and previous["status"] == "complete":
                logger.debug("cluster date=%s unchanged; preserving cluster IDs and labels", day)
                return "cached"
            if scheduled and previous["status"] in {"partial", "error"} and time.time() - previous["updated_at"] < 1800:
                return "backoff"

        import numpy as np
        import hdbscan
        valid, vectors = [], []
        for row in rows:
            try:
                raw = row["embedding"]
                if raw is None:
                    counts["skipped"] += 1
                    continue
                vector = np.frombuffer(raw, dtype="<f4").astype(np.float64)
                norm = float(np.linalg.norm(vector))
                if vector.shape != (384,) or not np.isfinite(vector).all() or norm <= 0:
                    raise ValueError("Invalid embedding")
                if not evidence_text(row["caption"], row["ocr_text"]):
                    raise ValueError("No lexical text evidence")
                vectors.append(vector / norm)
                valid.append({**row, "caption": usable_text(row["caption"]),
                              "ocr_text": usable_text(row["ocr_text"])})
            except Exception as error:
                counts["skipped"] += 1
                logger.warning("cluster skipped reel=%s reason=%s", row["shortcode"], type(error).__name__)
        labels = np.full(len(valid), -1, dtype=int)
        if len(valid) >= max(config["min_size"], config["min_samples"] + 1):
            matrix = np.stack(vectors)
            # Euclidean distance between unit vectors preserves cosine-distance
            # ordering; no dimensionality reduction or forced topic assignments.
            labels = hdbscan.HDBSCAN(min_cluster_size=config["min_size"],
                min_samples=config["min_samples"], metric="euclidean",
                core_dist_n_jobs=1, approx_min_span_tree=False,
                allow_single_cluster=False).fit_predict(matrix)
        counts["noise"] = int((labels == -1).sum())
        groups = []
        service_failed = False
        for label in sorted(set(labels) - {-1}):
            indices = np.flatnonzero(labels == label).tolist()
            try:
                center = np.mean([vectors[i] for i in indices], axis=0)
                representative = sorted(indices, key=lambda i: float(np.linalg.norm(vectors[i] - center)))[:6]
                examples = [{"caption": (valid[i]["caption"] or "")[:550],
                             "ocr_text": (valid[i]["ocr_text"] or "")[:350]} for i in representative]
                renew_lock(day, owner)
                if service_failed:
                    raise RuntimeError("LLM unavailable earlier in this run")
                generated = label_cluster(examples, len(indices), config["model"], config["endpoint"])
                title, summary = generated.label, generated.summary
            except Exception as error:
                counts["llm_failures"] += 1
                # Skip this labeling stage, retain the geometric group and keep
                # later groups processable. Do not invent a topic on failure.
                title = f"Unlabeled group {len(groups) + 1}"
                summary = f"{len(indices)} reels grouped by text similarity; automatic summary unavailable."
                logger.warning("cluster label skipped date=%s group=%s reason=%s", day, label, str(error)[:180])
                if isinstance(error, (OSError, TimeoutError)):
                    service_failed = True
            groups.append((indices, title, summary))
        counts["clusters"] = len(groups)
        renew_lock(day, owner)
        with closing(connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute("SELECT owner FROM cluster_locks WHERE session_date=?", (str(day),)).fetchone()
            if not lease or lease[0] != owner:
                raise RuntimeError("Clustering lease lost")
            if fingerprint(snapshot(connection, bounds), config) != signature:
                record_run(connection, day, config, signature, "stale", counts, "Inputs changed; rerun after understanding finishes")
                logger.info("cluster date=%s inputs changed; previous clusters preserved; rerun", day)
                return "stale"
            # Replace a changed day's output atomically; cache hits do not touch IDs.
            connection.execute("UPDATE reels SET cluster_id=NULL WHERE cluster_id IN (SELECT id FROM clusters WHERE session_date=?)", (str(day),))
            connection.execute("UPDATE reels SET cluster_id=NULL WHERE captured_at>=? AND captured_at<?", bounds)
            connection.execute("DELETE FROM clusters WHERE session_date=?", (str(day),))
            for indices, title, summary in groups:
                cluster_id = connection.execute(
                    "INSERT INTO clusters(session_date,label,summary,reel_count) VALUES (?,?,?,?)",
                    (str(day), title, summary, len(indices)),
                ).lastrowid
                connection.executemany("UPDATE reels SET cluster_id=? WHERE id=?",
                                       [(cluster_id, valid[i]["id"]) for i in indices])
            status = "partial" if counts["llm_failures"] else "complete"
            record_run(connection, day, config, signature, status, counts)
        logger.info("cluster date=%s status=%s clusters=%d noise=%d skipped=%d llm_failures=%d",
                    day, status, counts["clusters"], counts["noise"], counts["skipped"], counts["llm_failures"])
        return status
    except Exception as error:
        logger.exception("cluster date=%s failed; existing groups retained", day)
        try:
            with closing(connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = connection.execute("SELECT owner FROM cluster_locks WHERE session_date=?", (str(day),)).fetchone()
                if lease and lease[0] == owner:
                    record_run(connection, day, config, signature, "error", counts, f"{type(error).__name__}: {str(error)[:240]}")
        except Exception:
            logger.exception("Could not persist clustering failure")
        return "error"
    finally:
        try:
            with closing(connect()) as connection, connection:
                connection.execute("DELETE FROM cluster_locks WHERE session_date=? AND owner=?", (str(day), owner))
        except Exception:
            logger.warning("Cluster lease cleanup deferred until expiry")


def show_day(day):
    with closing(connect()) as connection:
        run = connection.execute("SELECT * FROM cluster_runs WHERE session_date=?", (str(day),)).fetchone()
        if run:
            print(f"{day}: status={run['status']} clusters={run['cluster_count']} noise={run['noise_count']} skipped={run['skipped_count']} llm_failures={run['llm_failures']}")
            if run["error"]:
                print("Reason:", run["error"])
        else:
            print(f"{day}: no clustering run yet")
        for row in connection.execute("SELECT label,reel_count,summary FROM clusters WHERE session_date=? ORDER BY reel_count DESC,id", (str(day),)):
            print(f"\n{row['label']} | {row['reel_count']} reels\n{row['summary']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="today", help="today, yesterday, or YYYY-MM-DD in DIGEST_TIMEZONE")
    parser.add_argument("--show", action="store_true", help="Print saved clusters without rerunning")
    parser.add_argument("--force", action="store_true", help="Regenerate labels even if inputs are unchanged")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = settings()
        today = datetime.now(ZoneInfo(config["timezone"])).date()
        day = today if args.date == "today" else today - timedelta(days=1) if args.date == "yesterday" else date.fromisoformat(args.date)
        if day > today:
            raise ValueError("Cannot cluster a future date")
        initialize(recover_jobs=False)
        status = "show" if args.show else run_day(day, config, args.force)
        show_day(day)
        return 1 if status in {"error", "partial", "stale", "busy"} else 0
    except Exception as error:
        logger.error("Clustering command skipped: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
