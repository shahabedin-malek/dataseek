"""Additive v2 schema. Never drops or rewrites the legacy tables."""
from __future__ import annotations

import sqlite3

V2_SCHEMA = """
PRAGMA foreign_keys = ON;

-- One row per pipeline generation (e.g. v2_multi_ocr).
CREATE TABLE IF NOT EXISTS processing_runs (
    run_id INTEGER PRIMARY KEY,
    processing_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tool_versions TEXT,
    notes TEXT
);

-- One row per OCR engine invocation on one image.
CREATE TABLE IF NOT EXISTS ocr_runs (
    ocr_run_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    engine TEXT NOT NULL,
    engine_version TEXT,
    preprocessing TEXT,
    confidence REAL,
    char_count INTEGER NOT NULL DEFAULT 0,
    box_count INTEGER NOT NULL DEFAULT 0,
    text TEXT,
    payload TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, engine, preprocessing)
);

-- Structured OCR regions (bounding boxes) for auditability.
CREATE TABLE IF NOT EXISTS ocr_regions (
    region_id INTEGER PRIMARY KEY,
    ocr_run_id INTEGER NOT NULL REFERENCES ocr_runs(ocr_run_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    engine TEXT NOT NULL,
    text TEXT,
    confidence REAL,
    x0 INTEGER, y0 INTEGER, x1 INTEGER, y1 INTEGER,
    region_index INTEGER NOT NULL
);

-- Vision-model reading per image.
CREATE TABLE IF NOT EXISTS vision_runs (
    vision_run_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    model TEXT NOT NULL,
    screenshot_type TEXT,
    visible_text TEXT,
    urls TEXT,
    entities TEXT,
    raw_response TEXT,
    latency_s REAL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, model)
);

-- Reconciled final result per image.
CREATE TABLE IF NOT EXISTS ocr_consensus (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    final_text TEXT,
    status TEXT,
    confidence REAL,
    agreement REAL,
    engines TEXT,
    escalation_level INTEGER NOT NULL DEFAULT 1,
    quality_level INTEGER NOT NULL DEFAULT 0,
    screenshot_type TEXT,
    updated_at TEXT NOT NULL
);

-- Every extracted candidate URL with its source and verification state.
CREATE TABLE IF NOT EXISTS urls (
    url_id INTEGER PRIMARY KEY,
    task_id TEXT REFERENCES tasks(task_id),
    entity_id TEXT REFERENCES entities(entity_id),
    url TEXT NOT NULL,
    url_type TEXT,
    evidence TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, url)
);

-- Category taxonomy (hierarchical).
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES categories(category_id),
    description TEXT,
    UNIQUE(name, parent_id)
);

-- Tags.
CREATE TABLE IF NOT EXISTS tags (
    tag_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS resource_tags (
    entity_id TEXT NOT NULL REFERENCES entities(entity_id),
    tag_id INTEGER NOT NULL REFERENCES tags(tag_id),
    PRIMARY KEY(entity_id, tag_id)
);

-- Features claimed by a resource with the source that supports them.
CREATE TABLE IF NOT EXISTS features (
    feature_id INTEGER PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id),
    name TEXT NOT NULL,
    evidence TEXT,
    source_kind TEXT,
    UNIQUE(entity_id, name)
);

-- Technologies used by a resource.
CREATE TABLE IF NOT EXISTS technologies (
    technology_id INTEGER PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id),
    name TEXT NOT NULL,
    evidence TEXT,
    UNIQUE(entity_id, name)
);

-- Explicit evidence log with source labelling (SCREENSHOT FACT, OCR EVIDENCE, ...).
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id INTEGER PRIMARY KEY,
    task_id TEXT REFERENCES tasks(task_id),
    entity_id TEXT REFERENCES entities(entity_id),
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    content TEXT,
    created_at TEXT NOT NULL
);

-- Near-duplicate relationships (perceptual / OCR / entity similarity).
CREATE TABLE IF NOT EXISTS duplicate_links (
    duplicate_link_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    other_task_id TEXT NOT NULL REFERENCES tasks(task_id),
    method TEXT NOT NULL,
    score REAL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, other_task_id, method)
);

-- Pipeline errors, one row per failure for recovery.
CREATE TABLE IF NOT EXISTS processing_errors (
    error_id INTEGER PRIMARY KEY,
    task_id TEXT,
    stage TEXT NOT NULL,
    message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ocr_runs_task ON ocr_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_urls_task ON urls(task_id);
CREATE INDEX IF NOT EXISTS idx_urls_url ON urls(url);
CREATE INDEX IF NOT EXISTS idx_dup_task ON duplicate_links(task_id);
CREATE INDEX IF NOT EXISTS idx_errors_task ON processing_errors(task_id);
"""

# Columns the v2 pipeline adds to the legacy tasks table for checkpointing.
TASK_COLUMNS = [
    ("processing_version", "TEXT"),
    ("v2_status", "TEXT"),
    ("ocr_status", "TEXT"),
    ("vision_status", "TEXT"),
    ("quality_level", "INTEGER DEFAULT 0"),
    ("screenshot_type", "TEXT"),
    ("ocr_confidence", "REAL"),
    ("updated_at", "TEXT"),
]

# Column additions to entities for richer knowledge records.
ENTITY_COLUMNS = [
    ("primary_category", "TEXT"),
    ("secondary_categories", "TEXT"),
    ("tags", "TEXT"),
    ("resource_type", "TEXT"),
    ("developer", "TEXT"),
    ("license", "TEXT"),
    ("platforms", "TEXT"),
    ("source_count", "INTEGER DEFAULT 0"),
    ("quality_level", "INTEGER DEFAULT 0"),
    ("processing_version", "TEXT"),
    ("updated_at", "TEXT"),
]


def _add_columns(db: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_v2_schema(db: sqlite3.Connection) -> None:
    """Create v2 tables and add the v2 tracking columns idempotently."""
    db.executescript(V2_SCHEMA)
    _add_columns(db, "tasks", TASK_COLUMNS)
    _add_columns(db, "entities", ENTITY_COLUMNS)
    # Seed the taxonomy root categories if the table is empty.
    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        seed_taxonomy(db)
    db.commit()


ROOT_CATEGORIES = [
    "AI", "Development", "Applications", "Web", "Data", "Security",
    "Media", "Documents", "Commands", "Other",
]


def seed_taxonomy(db: sqlite3.Connection) -> None:
    for name in ROOT_CATEGORIES:
        db.execute(
            "INSERT OR IGNORE INTO categories(name, parent_id, description) VALUES(?,NULL,?)",
            (name, f"Root category: {name}"),
        )
