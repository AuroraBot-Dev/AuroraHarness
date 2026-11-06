CREATE TABLE tool_calls (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
 task_key TEXT NOT NULL,
 tool_name TEXT NOT NULL,
 input_json TEXT NOT NULL,
 output TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL,
 started_at TEXT NOT NULL,
 ended_at TEXT,
 duration_ms INTEGER
);
CREATE INDEX tool_calls_run ON tool_calls(run_id,started_at);
