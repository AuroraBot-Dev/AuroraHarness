ALTER TABLE tasks ADD COLUMN depends_on_json TEXT;
ALTER TABLE tasks ADD COLUMN blocked_by_json TEXT NOT NULL DEFAULT '[]';
