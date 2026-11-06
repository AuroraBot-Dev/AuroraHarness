ALTER TABLE interactions ADD COLUMN expires_at TEXT;
ALTER TABLE interactions ADD COLUMN resolved_at TEXT;
ALTER TABLE interactions ADD COLUMN resolution TEXT;
CREATE INDEX interactions_expiry ON interactions(status,expires_at) WHERE kind='approval';
