"""One scheduled invocation: understand -> cluster -> Discord, for one capture day."""
import argparse
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime import configure
from .db import BASE_DIR, connect, initialize
from .process_lock import process_lock

PROJECT = BASE_DIR.parent
LOGS = BASE_DIR / 'data' / 'logs'
LAST_RUN = BASE_DIR / 'data' / 'nightly-last.json'
logger = logging.getLogger('reels.nightly')


def save_summary(summary):
    temporary = LAST_RUN.with_suffix('.tmp')
    temporary.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    temporary.replace(LAST_RUN)


def setup_logging():
    LOGS.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    stream = logging.StreamHandler()
    rotating = logging.handlers.RotatingFileHandler(
        LOGS / 'nightly.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    for handler in (stream, rotating):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def cleanup_logs():
    # Only this runner's stage artifacts; never the DB, frames or summary log.
    cutoff = time.time() - 30 * 86400
    for path in LOGS.glob('run-*'):
        if path.suffix in {'.log', '.json', '.tmp'} and path.is_file() and path.stat().st_mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                logger.warning('Old stage log could not be removed: %s', path.name)


def run_stage(name, day, run_id, max_jobs, new_batch=False):
    stem = f'run-{run_id}-{name}'
    result_path = LOGS / (stem + '.json')
    log_path = LOGS / (stem + '.log')
    command = [sys.executable, '-u', '-m', 'backend.nightly_stages', name,
               '--date', str(day), '--result', str(result_path), '--max-jobs', str(max_jobs)]
    if new_batch and name == 'share':
        command.append('--new-batch')
    timeout = 1800 if name in {'understand', 'cluster'} else 900
    logger.info('stage=%s status=started date=%s log=%s', name, day, log_path.name)
    environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    try:
        with log_path.open('w', encoding='utf-8') as output:
            completed = subprocess.run(command, cwd=PROJECT, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding='utf-8'))
        else:
            result = {'status': 'error', 'reason': f'Stage exited without result (exit {completed.returncode})'}
    except subprocess.TimeoutExpired:
        # subprocess.run kills/waits for its child. Understanding's OS lock is
        # released; a committed Discord send intent will remain uncertain.
        result = {'status': 'deferred', 'reason': 'Stage time limit reached; inspect log before retrying'}
    except Exception as error:
        result = {'status': 'error', 'reason': type(error).__name__}
    logger.info('stage=%s status=%s detail=%s', name, result['status'], json.dumps(result))
    return result


def run(day, max_jobs, run_id, new_batch=False):
    initialize(recover_jobs=False)
    completed_digest = False
    with closing(connect()) as connection:
        digest = connection.execute('SELECT id FROM digests WHERE date=?', (str(day),)).fetchone()
        if digest:
            statuses = [row[0] for row in connection.execute(
                'SELECT status FROM digest_items WHERE digest_id=?', (digest[0],))]
            completed_digest = bool(statuses) and all(status == 'sent' for status in statuses)
            if completed_digest and not new_batch:
                return {'status': 'already_sent', 'date': str(day), 'stages': {}}
    stages = {}
    # Resume unfinished picks verbatim. Only an explicit new-batch request
    # after completion processes additional unshared captures from this date.
    if not digest or (new_batch and completed_digest):
        stages['understand'] = run_stage('understand', day, run_id, max_jobs)
        if stages['understand']['status'] in {'deferred', 'error'}:
            return {'status': stages['understand']['status'], 'date': str(day), 'stages': stages}
        stages['cluster'] = run_stage('cluster', day, run_id, max_jobs)
        if stages['cluster']['status'] in {'deferred', 'error'}:
            return {'status': stages['cluster']['status'], 'date': str(day), 'stages': stages}
    stages['share'] = run_stage('share', day, run_id, max_jobs, new_batch=new_batch)
    status = stages['share']['status']
    if status in {'complete', 'no_digest'} and any(value['status'] == 'degraded' for value in stages.values()):
        status = 'degraded'
    return {'status': status, 'date': str(day), 'stages': stages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default='yesterday', help='yesterday (default), today, or YYYY-MM-DD')
    parser.add_argument('--new-batch', action='store_true', help='Explicit additional batch of unshared reels after the previous batch completed')
    parser.add_argument('--max-jobs', type=int, default=500, help='Understanding work limit for this invocation')
    args = parser.parse_args()
    setup_logging()
    try:
        with process_lock('nightly') as acquired:
            if not acquired:
                logger.info('nightly status=busy reason=another_run_active; no work started')
                return 2
            run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            summary = {'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(),
                       'status': 'running', 'date': args.date, 'new_batch_requested': args.new_batch}
            save_summary(summary)
            try:
                configure()
                if not 1 <= args.max_jobs <= 5000:
                    raise ValueError('max-jobs must be between 1 and 5000')
                today = datetime.now(ZoneInfo(os.environ.get('DIGEST_TIMEZONE', 'Asia/Karachi'))).date()
                day = today if args.date == 'today' else today - timedelta(days=1) if args.date == 'yesterday' else date.fromisoformat(args.date)
                if day > today:
                    raise ValueError('Cannot process a future date')
                cleanup_logs()
                logger.info('nightly status=started date=%s run_id=%s', day, run_id)
                summary.update(run(day, args.max_jobs, run_id, new_batch=args.new_batch))
            except Exception as error:
                summary.update(status='error', reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
                logger.error('nightly status=error reason=%s', summary['reason'])
            summary['finished_at'] = datetime.now(timezone.utc).isoformat()
            save_summary(summary)
            logger.info('nightly date=%s status=%s summary=%s', summary['date'], summary['status'], LAST_RUN)
            return 0 if summary['status'] in {'complete', 'already_sent', 'no_digest'} else 2 if summary['status'] == 'deferred' else 1
    except Exception as error:
        logger.error('nightly status=error reason=%s; check local data-directory access', type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
