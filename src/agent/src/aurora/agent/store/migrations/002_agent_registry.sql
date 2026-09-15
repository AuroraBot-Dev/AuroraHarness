ALTER TABLE model_configs ADD COLUMN model_type TEXT NOT NULL DEFAULT 'text' CHECK(model_type IN ('text','multimodal','embedding'));
UPDATE model_configs SET model_type='multimodal' WHERE input_types_json LIKE '%image%';
ALTER TABLE agents ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN callable INTEGER NOT NULL DEFAULT 0 CHECK(callable IN (0,1));
ALTER TABLE agents ADD COLUMN test_result_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE agent_runs ADD COLUMN parent_task_id TEXT REFERENCES tasks(id);
ALTER TABLE agent_runs ADD COLUMN config_snapshot_json TEXT NOT NULL DEFAULT '{}';
CREATE INDEX agent_runs_agent_status ON agent_runs(agent_id,status);
