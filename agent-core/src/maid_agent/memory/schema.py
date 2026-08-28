SCHEMA_SQL = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS world_locations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  dimension TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 1.0,
  first_seen_tick INTEGER NOT NULL DEFAULT 0,
  last_seen_tick INTEGER NOT NULL DEFAULT 0,
  last_seen_day INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_location_dimension ON world_locations(dimension,last_seen_tick);

CREATE TABLE IF NOT EXISTS resource_observations (
  resource_id TEXT PRIMARY KEY,
  block_id TEXT NOT NULL,
  dimension TEXT NOT NULL,
  x INTEGER NOT NULL, y INTEGER NOT NULL, z INTEGER NOT NULL,
  observed_exposed INTEGER NOT NULL DEFAULT 1,
  estimated_count INTEGER NOT NULL DEFAULT 1,
  first_seen INTEGER NOT NULL DEFAULT 0,
  last_seen INTEGER NOT NULL DEFAULT 0,
  last_seen_day INTEGER NOT NULL DEFAULT 0,
  exhausted INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(block_id,dimension,x,y,z)
);
CREATE INDEX IF NOT EXISTS idx_resource_lookup ON resource_observations(dimension,block_id,exhausted,last_seen);

CREATE TABLE IF NOT EXISTS structures (
  structure_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  dimension TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'KNOWN',
  first_seen_tick INTEGER NOT NULL DEFAULT 0,
  last_seen_tick INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_structures ON structures(dimension,kind,last_seen_tick);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  event_key TEXT UNIQUE,
  game_day INTEGER NOT NULL,
  game_tick INTEGER NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  position_json TEXT,
  payload_json TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'agent',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(game_day,game_tick);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type,game_day,game_tick);

CREATE TABLE IF NOT EXISTS goals (
  goal_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  objective TEXT NOT NULL,
  priority INTEGER NOT NULL,
  status TEXT NOT NULL,
  model_json TEXT NOT NULL,
  parent_goal_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status,updated_at);

CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  status TEXT NOT NULL,
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  model_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status,updated_at);

CREATE TABLE IF NOT EXISTS capability_gaps (
  gap_id TEXT PRIMARY KEY,
  desired_objective TEXT NOT NULL,
  expression_failure_reason TEXT NOT NULL,
  missing_capability_type TEXT NOT NULL,
  occurrence_count INTEGER NOT NULL DEFAULT 1,
  impact TEXT NOT NULL,
  last_game_day INTEGER NOT NULL DEFAULT 0,
  last_game_tick INTEGER NOT NULL DEFAULT 0,
  last_occurred_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN',
  model_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_capability_gaps_recent ON capability_gaps(status,last_game_day,last_game_tick);

CREATE TABLE IF NOT EXISTS strategy_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  model_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runtime_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skills (
  skill_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  source_path TEXT,
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  avg_duration REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by TEXT NOT NULL DEFAULT 'builtin',
  failure_codes_json TEXT NOT NULL DEFAULT '{}',
  goal_tags_json TEXT NOT NULL DEFAULT '[]',
  last_used_at TEXT,
  last_failure_code TEXT,
  PRIMARY KEY(skill_id,version)
);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status,name);

CREATE TABLE IF NOT EXISTS skill_refinement_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  reason TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'OPEN',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(skill_id,version,status)
);

CREATE TABLE IF NOT EXISTS threat_windows (
  window_id TEXT PRIMARY KEY,
  day INTEGER NOT NULL,
  period TEXT NOT NULL,
  started_tick INTEGER NOT NULL,
  ended_tick INTEGER,
  hostile_contacts INTEGER NOT NULL DEFAULT 0,
  unique_hostiles INTEGER NOT NULL DEFAULT 0,
  damage_taken REAL NOT NULL DEFAULT 0,
  deaths INTEGER NOT NULL DEFAULT 0,
  retreats INTEGER NOT NULL DEFAULT 0,
  base_damage_events INTEGER NOT NULL DEFAULT 0,
  targeting_peak INTEGER NOT NULL DEFAULT 0,
  entry_direction_json TEXT NOT NULL DEFAULT '{}',
  attacker_types_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_threat_day ON threat_windows(day,period);

CREATE TABLE IF NOT EXISTS rnd_cycles (
  cycle_id TEXT PRIMARY KEY,
  trigger_day INTEGER NOT NULL UNIQUE,
  runtime_period_start_day INTEGER NOT NULL,
  runtime_period_end_day INTEGER NOT NULL,
  token_budget INTEGER NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'READY',
  artifact_dir TEXT NOT NULL,
  source_workspace TEXT NOT NULL DEFAULT '',
  production_version TEXT NOT NULL DEFAULT '',
  source_hash TEXT NOT NULL DEFAULT '',
  phase TEXT NOT NULL DEFAULT 'DECIDING_DIRECTION',
  outcome TEXT,
  project_id TEXT NOT NULL DEFAULT '',
  project_size TEXT,
  continuation_decision TEXT NOT NULL DEFAULT 'NEW',
  budget_plan_json TEXT NOT NULL DEFAULT '{}',
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  project_state_json TEXT NOT NULL DEFAULT '{}',
  failure_state_json TEXT NOT NULL DEFAULT '{}',
  handled INTEGER NOT NULL DEFAULT 0,
  owner_pid INTEGER,
  owner_started_at TEXT,
  dsh_session_id TEXT NOT NULL DEFAULT '',
  dsh_version TEXT NOT NULL DEFAULT '',
  dsh_profile_version TEXT NOT NULL DEFAULT '',
  dsh_cli_version TEXT NOT NULL DEFAULT '',
  dsh_workspace TEXT NOT NULL DEFAULT '',
  dsh_current_phase TEXT NOT NULL DEFAULT '',
  dsh_phase_progress_json TEXT NOT NULL DEFAULT '{}',
  dsh_last_finish_reason TEXT NOT NULL DEFAULT '',
  dsh_last_event_at TEXT,
  baseline_commit TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS token_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ledger TEXT NOT NULL CHECK(ledger IN ('runtime','rnd')),
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL,
  request_id TEXT NOT NULL,
  estimated INTEGER NOT NULL DEFAULT 0,
  game_day INTEGER,
  cycle_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_ledger ON token_usage(ledger,created_at);
CREATE INDEX IF NOT EXISTS idx_token_cycle ON token_usage(ledger,cycle_id,game_day);

CREATE TABLE IF NOT EXISTS llm_requests (
  request_id TEXT PRIMARY KEY,
  ledger TEXT NOT NULL,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  http_status INTEGER,
  ok INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  estimated INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT '',
  cycle_id TEXT,
  game_day INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_requests ON llm_requests(created_at);

CREATE TABLE IF NOT EXISTS building_checkpoints (
  build_id TEXT PRIMARY KEY,
  blueprint_json TEXT NOT NULL,
  origin_json TEXT NOT NULL,
  rotation INTEGER NOT NULL DEFAULT 0,
  next_segment INTEGER NOT NULL DEFAULT 0,
  completed_blocks INTEGER NOT NULL DEFAULT 0,
  completed_indices_json TEXT NOT NULL DEFAULT '[]',
  skipped_optional_indices_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  missing_items_json TEXT NOT NULL DEFAULT '{}',
  last_error TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
