-- AEGIS fleet memory. Contract 2. Works on ClickHouse Cloud and on a local docker server.
CREATE DATABASE IF NOT EXISTS aegis;

CREATE TABLE IF NOT EXISTS aegis.scans (
  ts          DateTime64(3) DEFAULT now64(3),
  agent       LowCardinality(String),
  repo        LowCardinality(String),
  sha         String,
  base_sha    String DEFAULT '',
  trigger     LowCardinality(String),   -- push | pr | cron | backfill | manual
  n_files     UInt32,
  n_findings  UInt32,
  verdict     LowCardinality(String),   -- safe | unsafe
  semgrep_ms  UInt32,
  total_ms    UInt32
) ENGINE = MergeTree ORDER BY (repo, ts);

CREATE TABLE IF NOT EXISTS aegis.findings (
  ts          DateTime64(3) DEFAULT now64(3),
  agent       LowCardinality(String),
  repo        LowCardinality(String),
  sha         String,
  commit_ts   DateTime DEFAULT now(),   -- author time of the commit (backfill fills real history)
  fingerprint String,
  rule_id     LowCardinality(String),
  severity    LowCardinality(String),
  cwe         LowCardinality(String),
  path        String,
  line        UInt32,
  has_fix     UInt8,
  status      LowCardinality(String)    -- new | still_open | resolved | dismissed
) ENGINE = MergeTree ORDER BY (repo, fingerprint, ts);

CREATE TABLE IF NOT EXISTS aegis.actions (
  ts          DateTime64(3) DEFAULT now64(3),
  agent       LowCardinality(String),
  repo        LowCardinality(String),
  kind        LowCardinality(String),   -- status_set | issue_opened | issue_closed | pr_opened | pr_reviewed | dismissed | denied | email
  ref         String,
  fingerprint String DEFAULT '',
  latency_ms  UInt32 DEFAULT 0,
  session_url String DEFAULT ''
) ENGINE = MergeTree ORDER BY (repo, ts);
