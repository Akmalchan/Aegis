-- AEGIS real-time rollups (materialized views). Applied by aegis.ch.init() after schema.sql. Idempotent.
-- Base tables in schema.sql are frozen; everything here is additive.

-- one-time backfill markers (so re-running init never double-inserts history)
CREATE TABLE IF NOT EXISTS aegis.mv_backfill (
  name String,
  ts   DateTime64(3) DEFAULT now64(3)
) ENGINE = MergeTree ORDER BY name;

-- posture: distinct open fingerprints per (commit day, repo, severity); updated on every insert into findings
CREATE TABLE IF NOT EXISTS aegis.posture_daily (
  day      Date,
  repo     LowCardinality(String),
  severity LowCardinality(String),
  fps      AggregateFunction(uniq, String),
  rows     AggregateFunction(count)
) ENGINE = AggregatingMergeTree ORDER BY (day, repo, severity);

CREATE MATERIALIZED VIEW IF NOT EXISTS aegis.posture_daily_mv TO aegis.posture_daily AS
SELECT toDate(commit_ts) AS day, repo, severity, uniqState(fingerprint) AS fps, countState() AS rows
FROM aegis.findings WHERE status IN ('new', 'still_open')
GROUP BY day, repo, severity;

-- agent activity: actions per minute per (agent, kind); updated on every insert into actions
CREATE TABLE IF NOT EXISTS aegis.agent_activity_1m (
  minute DateTime,
  agent  LowCardinality(String),
  kind   LowCardinality(String),
  n      UInt64
) ENGINE = SummingMergeTree ORDER BY (agent, kind, minute);

CREATE MATERIALIZED VIEW IF NOT EXISTS aegis.agent_activity_mv TO aegis.agent_activity_1m AS
SELECT toStartOfMinute(ts) AS minute, agent, kind, count() AS n
FROM aegis.actions GROUP BY minute, agent, kind;

-- one-time backfill of history that predates the MVs.
-- posture: uniq states are idempotent, so the full table is safe (backfill rows carry load time in ts, not insert order).
INSERT INTO aegis.posture_daily
SELECT toDate(commit_ts) AS day, repo, severity, uniqState(fingerprint), countState()
FROM aegis.findings
WHERE status IN ('new', 'still_open') AND (SELECT count() FROM aegis.mv_backfill WHERE name = 'posture_daily') = 0
GROUP BY day, repo, severity;

INSERT INTO aegis.mv_backfill (name)
SELECT 'posture_daily' WHERE (SELECT count() FROM aegis.mv_backfill WHERE name = 'posture_daily') = 0;

-- activity: counts are additive, so only rows inserted before the MV existed (ts < MV creation time)
INSERT INTO aegis.agent_activity_1m
SELECT toStartOfMinute(ts) AS minute, agent, kind, count() AS n
FROM aegis.actions
WHERE ts < (SELECT max(metadata_modification_time) FROM system.tables
            WHERE database = 'aegis' AND name = 'agent_activity_mv')
  AND (SELECT count() FROM aegis.mv_backfill WHERE name = 'agent_activity_1m') = 0
GROUP BY minute, agent, kind;

INSERT INTO aegis.mv_backfill (name)
SELECT 'agent_activity_1m' WHERE (SELECT count() FROM aegis.mv_backfill WHERE name = 'agent_activity_1m') = 0;
