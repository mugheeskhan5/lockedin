"""Persistent non-secret settings shared by the backend and scheduled commands."""
import json
import os
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / 'runtime.local.json'
ALLOWED = {
    'DIGEST_TIMEZONE', 'OLLAMA_MODEL', 'OLLAMA_URL', 'CLUSTER_MIN_SIZE',
    'CLUSTER_MIN_SAMPLES', 'OCR_LANG', 'TESSERACT_CMD', 'DIGEST_CPU_THREADS',
}


def configure():
    if SETTINGS_PATH.is_file():
        try:
            values = json.loads(SETTINGS_PATH.read_text(encoding='utf-8-sig'))
            if not isinstance(values, dict) or set(values) - ALLOWED:
                raise ValueError()
            if any(not isinstance(value, (str, int)) for value in values.values()):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError('Invalid backend/runtime.local.json; use only keys from runtime.example.json') from None
        for key, value in values.items():
            os.environ.setdefault(key, str(value))
    threads = int(os.environ.get('DIGEST_CPU_THREADS', '2'))
    if not 1 <= threads <= 4:
        raise ValueError('DIGEST_CPU_THREADS must be between 1 and 4')
    os.environ['DIGEST_CPU_THREADS'] = str(threads)
    # These are per-process limits, not system-wide environment changes.
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = str(threads)
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    return threads
