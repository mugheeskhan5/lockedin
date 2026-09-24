CREATE TABLE IF NOT EXISTS reels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode TEXT UNIQUE NOT NULL,
    permalink TEXT NOT NULL,
    creator TEXT,
    caption TEXT,
    ocr_text TEXT,
    thumbnail_url TEXT,
    captured_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    embedding BLOB,
    cluster_id INTEGER,
    shared BOOLEAN DEFAULT 0,
    shared_at DATETIME
);
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date DATE NOT NULL,
    label TEXT,
    summary TEXT,
    reel_count INTEGER
);
CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    content_md TEXT,
    sent_to_discord BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reels_captured_at ON reels(captured_at);

-- Durable understanding state; the three requested tables retain their schema.
CREATE TABLE IF NOT EXISTS understanding_jobs (
    reel_id INTEGER PRIMARY KEY REFERENCES reels(id),
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    frame BLOB,
    frame_status TEXT NOT NULL DEFAULT 'absent',
    ocr_status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_understanding_status ON understanding_jobs(status, reel_id);

CREATE TABLE IF NOT EXISTS cluster_runs (
    session_date DATE PRIMARY KEY,
    timezone TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    cluster_count INTEGER DEFAULT 0,
    noise_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    llm_failures INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 1,
    updated_at REAL NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS cluster_locks (
    session_date DATE PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_versions (
    name TEXT PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reel_thumbnails (
    reel_id INTEGER PRIMARY KEY REFERENCES reels(id),
    image_bytes BLOB NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_date ON digests(date);
CREATE TABLE IF NOT EXISTS digest_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_id INTEGER NOT NULL REFERENCES digests(id),
    reel_id INTEGER UNIQUE NOT NULL REFERENCES reels(id),
    position INTEGER NOT NULL,
    discord_target_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    photo_blob BLOB,
    photo_disabled BOOLEAN DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'prepared',
    attempts INTEGER DEFAULT 0,
    not_before REAL DEFAULT 0,
    message_id INTEGER,
    last_error TEXT,
    sent_at DATETIME,
    UNIQUE(digest_id,position)
);
CREATE TABLE IF NOT EXISTS sharing_lock (
    id INTEGER PRIMARY KEY CHECK(id=1),
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
