"""Internal stage entrypoints: each exits to release its model/native memory."""
import argparse
import asyncio
import json
import logging
import time
from contextlib import closing
from datetime import date
from pathlib import Path

from .runtime import configure
from .db import connect, initialize
from .process_lock import process_lock

logger = logging.getLogger('uvicorn.error')


def understanding_counts(bounds):
    with closing(connect()) as connection:
        return dict(connection.execute('''
            SELECT j.status,COUNT(*) FROM understanding_jobs j JOIN reels r ON r.id=j.reel_id
            WHERE r.shared=0 AND r.captured_at>=? AND r.captured_at<? GROUP BY j.status
        ''', bounds).fetchall())


def understand(day, max_jobs, minutes):
    from .clustering import day_bounds, settings
    from .understanding import process_next
    bounds = day_bounds(day, settings()['timezone'])
    # One retry pass per invocation, never an infinite loop over a bad row.
    # Text-free/skipped rows remain skipped until ingest supplies better input.
    with closing(connect()) as connection, connection:
        retried = connection.execute('''
            UPDATE understanding_jobs SET status='pending',updated_at=CURRENT_TIMESTAMP
            WHERE status='error' AND attempts<3 AND reel_id IN (
                SELECT id FROM reels WHERE shared=0 AND captured_at>=? AND captured_at<?)
        ''', bounds).rowcount
    logger.info('understanding date=%s retry_rows=%d max_jobs=%d', day, retried, max_jobs)
    end = time.monotonic() + minutes * 60
    processed = 0
    while processed < max_jobs and time.monotonic() < end:
        try:
            worked = process_next(bounds, unshared_only=True)
        except Exception as error:
            logger.warning('understanding queue deferred reason=%s', type(error).__name__)
            return {'status': 'deferred', 'processed': processed, 'reason': type(error).__name__}
        if worked:
            processed += 1
            continue
        counts = understanding_counts(bounds)
        if not counts.get('pending') and not counts.get('processing'):
            break
        # Another live worker has the OS lock. Let it finish instead of loading
        # a second model concurrently or claiming the same row.
        time.sleep(1)
    counts = understanding_counts(bounds)
    pending = counts.get('pending', 0) + counts.get('processing', 0)
    status = 'deferred' if pending else 'degraded' if counts.get('error') else 'complete'
    logger.info('understanding date=%s status=%s processed=%d counts=%s', day, status, processed, counts)
    return {'status': status, 'processed': processed, 'counts': counts}


def cluster(day):
    from .clustering import run_day, settings
    status = run_day(day, settings(), scheduled=False)
    return {'status': {'complete': 'complete', 'cached': 'complete', 'partial': 'degraded',
                       'busy': 'deferred', 'stale': 'deferred'}.get(status, 'error'),
            'cluster_status': status}


def share(day, new_batch=False):
    import uuid
    from . import sharing
    from .discord_config import load_config
    config = load_config()
    owner = uuid.uuid4().hex
    if not sharing.acquire(owner):
        return {'status': 'deferred', 'reason': 'Another sharing job is running'}
    try:
        digest_id = sharing.prepare(day, config, owner, new_batch=new_batch)
        if digest_id is None:
            return {'status': 'no_digest', 'reason': 'Fewer than 3 eligible distinct text-bearing picks'}
        sharing.bind_legacy_unsent(digest_id, config, owner)
        sent_before = sum(row['status'] == 'sent' for row in sharing.items_for(digest_id))
        asyncio.run(sharing.deliver(digest_id, config, owner))
        rows = sharing.items_for(digest_id)
        counts = {status: sum(row['status'] == status for row in rows)
                  for status in ('prepared', 'sending', 'sent', 'failed', 'unknown')}
        return {'status': 'complete' if rows and counts['sent'] == len(rows) else 'incomplete',
                'digest_id': digest_id, 'delivery': counts, 'newly_sent': counts['sent'] - sent_before}
    finally:
        try:
            with closing(connect()) as connection, connection:
                connection.execute('DELETE FROM sharing_lock WHERE id=1 AND owner=?', (owner,))
        except Exception:
            logger.warning('Sharing lease cleanup deferred until expiry')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('understand', 'cluster', 'share'))
    parser.add_argument('--date', required=True)
    parser.add_argument('--result', required=True)
    parser.add_argument('--max-jobs', type=int, default=500)
    parser.add_argument('--minutes', type=int, default=20)
    parser.add_argument('--new-batch', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('discord').setLevel(logging.WARNING)
    try:
        configure()
        initialize(recover_jobs=False)
        with process_lock('nightly-stage') as acquired:
            if not acquired:
                result = {'status': 'deferred', 'reason': 'Another nightly stage is still running'}
            else:
                day = date.fromisoformat(args.date)
                result = (understand(day, args.max_jobs, args.minutes) if args.stage == 'understand'
                          else cluster(day) if args.stage == 'cluster' else share(day, new_batch=args.new_batch))
    except Exception as error:
        # HTTP exceptions and credentials never appear in raw tracebacks.
        logger.error('stage=%s status=error reason=%s', args.stage, type(error).__name__)
        result = {'status': 'error', 'reason': type(error).__name__}
    result['stage'] = args.stage
    result['date'] = args.date
    output = Path(args.result)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2), encoding='utf-8')
    temporary.replace(output)
    logger.info('stage=%s result=%s', args.stage, json.dumps(result))
    return 0 if result['status'] in {'complete', 'no_digest'} else 2 if result['status'] == 'deferred' else 1


if __name__ == '__main__':
    raise SystemExit(main())
