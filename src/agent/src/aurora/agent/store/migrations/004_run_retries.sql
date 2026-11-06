ALTER TABLE runs ADD COLUMN retry_of_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL;
ALTER TABLE runs ADD COLUMN retry_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN retry_of_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;
CREATE INDEX runs_retry_source ON runs(retry_of_run_id);
