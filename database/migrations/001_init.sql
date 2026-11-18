-- Reference schema for PostgreSQL. The app also creates tables automatically on startup
-- (SQLAlchemy create_all). Use this file if you prefer managing the schema by hand.
CREATE EXTENSION IF NOT EXISTS vector;
-- Tables: projects, records(embedding vector(256)), memories, relationships,
-- sync_state, ai_usage, analysis_results  -> see database/models.py for columns.
-- Optional ANN index once you have many records:
-- CREATE INDEX IF NOT EXISTS ix_records_embedding ON records USING hnsw (embedding vector_cosine_ops);
