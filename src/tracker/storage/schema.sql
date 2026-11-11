-- All *_at columns are UTC, stored as TIMESTAMP (no time zone).
-- Every statement is idempotent so init can run on every start.

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------- app (operational state)
CREATE TABLE IF NOT EXISTS app.items (
    item_id       VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    query         VARCHAR NOT NULL,        -- search term or product URL
    target_price  DECIMAL(12, 2),
    currency      VARCHAR NOT NULL DEFAULT 'USD',
    sources       VARCHAR[] NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS app.listings (
    listing_id         VARCHAR PRIMARY KEY,  -- '<source>:<source_listing_id>'
    source             VARCHAR NOT NULL,
    source_listing_id  VARCHAR NOT NULL,
    title              VARCHAR NOT NULL,
    url                VARCHAR NOT NULL,
    currency           VARCHAR NOT NULL,
    first_seen_at      TIMESTAMP NOT NULL,
    last_seen_at       TIMESTAMP NOT NULL
);

-- Which listings belong to which tracked item (refined by product matching later).
CREATE TABLE IF NOT EXISTS app.item_listings (
    item_id           VARCHAR NOT NULL,
    listing_id        VARCHAR NOT NULL,
    matched_by        VARCHAR NOT NULL DEFAULT 'search',
    match_confidence  DOUBLE,
    created_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (item_id, listing_id)
);

-- ---------------------------------------------------------------- bronze (raw, append-only)
CREATE TABLE IF NOT EXISTS bronze.ingest_runs (
    run_id            VARCHAR PRIMARY KEY,
    source            VARCHAR NOT NULL,
    kind              VARCHAR NOT NULL,      -- 'check' | 'backfill'
    status            VARCHAR NOT NULL,      -- 'running' | 'success' | 'partial' | 'failed'
    started_at        TIMESTAMP NOT NULL,
    finished_at       TIMESTAMP,
    n_attempted       INTEGER,
    n_parse_failures  INTEGER,
    n_loaded          INTEGER,
    n_duplicate       INTEGER,
    n_rejected        INTEGER,
    layout_broken     BOOLEAN,
    errors            VARCHAR[]
);

-- Validated observations. observation_key = hash(source, listing, check window),
-- so re-running the same window never creates duplicates.
CREATE TABLE IF NOT EXISTS bronze.raw_observations (
    observation_key    VARCHAR PRIMARY KEY,
    run_id             VARCHAR NOT NULL,
    listing_id         VARCHAR NOT NULL,
    source             VARCHAR NOT NULL,
    source_listing_id  VARCHAR NOT NULL,
    title              VARCHAR NOT NULL,
    url                VARCHAR NOT NULL,
    price              DECIMAL(12, 2) NOT NULL,
    shipping           DECIMAL(12, 2) NOT NULL,
    currency           VARCHAR NOT NULL,
    in_stock           BOOLEAN NOT NULL,
    raw_payload        JSON,
    fetched_at         TIMESTAMP NOT NULL,
    loaded_at          TIMESTAMP NOT NULL
);

-- Observations the validator refused, kept for data-quality reporting.
CREATE TABLE IF NOT EXISTS bronze.rejected_observations (
    observation_key  VARCHAR PRIMARY KEY,
    run_id           VARCHAR NOT NULL,
    listing_id       VARCHAR NOT NULL,
    source           VARCHAR NOT NULL,
    reason           VARCHAR NOT NULL,
    detail           VARCHAR NOT NULL,
    raw_price        VARCHAR,
    fetched_at       TIMESTAMP NOT NULL,
    detected_at      TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------- alerts
-- Custom alert rules (the built-in target / 90-day-low / 24h-drop / deal rules
-- live in code). item_id NULL = applies to every item.
CREATE TABLE IF NOT EXISTS app.rules (
    rule_id      VARCHAR PRIMARY KEY,
    item_id      VARCHAR,
    rule_type    VARCHAR NOT NULL,       -- below_price | pct_drop_24h | low_90d | genuine_deal
    threshold    DECIMAL(12, 4),
    raw_text     VARCHAR,                -- the natural-language request, if any
    created_via  VARCHAR NOT NULL DEFAULT 'cli',
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP NOT NULL
);

-- Every alert we tried to send. Used for de-duplication and as an audit log.
-- subject_id is an item_id, or 'source:<name>' for system alerts.
CREATE TABLE IF NOT EXISTS app.alerts_sent (
    alert_key   VARCHAR PRIMARY KEY,
    subject_id  VARCHAR NOT NULL,
    rule_id     VARCHAR NOT NULL,
    rule_type   VARCHAR NOT NULL,
    listing_id  VARCHAR,
    price       DECIMAL(12, 2),
    currency    VARCHAR,
    message     VARCHAR NOT NULL,
    channels    VARCHAR[] NOT NULL,
    status      VARCHAR NOT NULL,        -- sent | failed
    error       VARCHAR,
    sent_at     TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------- product matching
ALTER TABLE app.item_listings ADD COLUMN IF NOT EXISTS is_match BOOLEAN DEFAULT TRUE;
ALTER TABLE app.item_listings ADD COLUMN IF NOT EXISTS match_reason VARCHAR;

-- Every matching decision, for auditing (and so the LLM is never asked twice).
CREATE TABLE IF NOT EXISTS app.match_decisions (
    item_id      VARCHAR NOT NULL,
    listing_id   VARCHAR NOT NULL,
    item_name    VARCHAR NOT NULL,
    title        VARCHAR NOT NULL,
    is_match     BOOLEAN NOT NULL,
    method       VARCHAR NOT NULL,      -- rules | embedding | llm | embedding_fallback
    similarity   DOUBLE,
    confidence   DOUBLE,
    reason       VARCHAR,
    model        VARCHAR,
    decided_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (item_id, listing_id)
);
