PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    root_path TEXT,
    manifest_path TEXT,
    manifest_valid BOOLEAN,
    manifest_errors JSON NOT NULL DEFAULT '[]',
    sync_state VARCHAR(32) NOT NULL DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    sequence JSON NOT NULL DEFAULT '[]',
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS validators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_id INTEGER REFERENCES steps(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    phase VARCHAR(16) NOT NULL,
    run_once BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE(project_id, name),
    CHECK (phase IN ('before', 'after'))
);

CREATE TABLE IF NOT EXISTS parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
    step_id INTEGER REFERENCES steps(id) ON DELETE CASCADE,
    validator_id INTEGER REFERENCES validators(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    type_name VARCHAR(64),
    required BOOLEAN NOT NULL DEFAULT 0,
    default_value JSON,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS dataset_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    type_name VARCHAR(64),
    required BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS artifact_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    path_pattern TEXT NOT NULL,
    media_type VARCHAR(255),
    UNIQUE(project_id, name)
);
