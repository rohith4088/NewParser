-- DuckDB Schema for Support Bundle Sessions

CREATE TABLE IF NOT EXISTS bundle_metadata (
    id VARCHAR PRIMARY KEY,
    filename VARCHAR,
    scanned_at VARCHAR,
    min_ts_micros BIGINT,
    max_ts_micros BIGINT,
    total_events BIGINT DEFAULT 0,
    total_files INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    ts_micros BIGINT,
    ts_iso VARCHAR,
    source VARCHAR,
    node VARCHAR,
    namespace VARCHAR,
    pod VARCHAR,
    container VARCHAR,
    level VARCHAR,
    issue_type VARCHAR,
    message VARCHAR,
    file VARCHAR,
    line_num INTEGER,
    unit_id VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_micros);
CREATE INDEX IF NOT EXISTS idx_events_ns ON events(namespace);
CREATE INDEX IF NOT EXISTS idx_events_level ON events(level);
CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_type);
CREATE INDEX IF NOT EXISTS idx_events_unit ON events(unit_id);

CREATE TABLE IF NOT EXISTS parsed_units (
    unit_id VARCHAR PRIMARY KEY,
    part_id VARCHAR,
    status VARCHAR,
    files INTEGER DEFAULT 0,
    events BIGINT DEFAULT 0,
    started_at VARCHAR,
    finished_at VARCHAR,
    error VARCHAR
);

CREATE TABLE IF NOT EXISTS k8s_facts (
    ts_micros BIGINT,
    kind VARCHAR,
    namespace VARCHAR,
    name VARCHAR,
    reason VARCHAR,
    exit_code INTEGER,
    restarts INTEGER,
    detail VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_k8s_facts_reason ON k8s_facts(reason);
CREATE INDEX IF NOT EXISTS idx_k8s_facts_ns ON k8s_facts(namespace);

CREATE TABLE IF NOT EXISTS metrics (
    ts_micros BIGINT,
    node VARCHAR,
    metric_name VARCHAR,
    metric_value DOUBLE,
    unit VARCHAR,
    detail VARCHAR
);
