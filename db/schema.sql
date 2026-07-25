-- MediFlow-AI schema
-- Run with: psql -U <user> -d <db> -f db/schema.sql

CREATE TABLE IF NOT EXISTS doctors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    specialty TEXT,
    email TEXT UNIQUE,
    working_hours_start TIME DEFAULT '09:00',
    working_hours_end TIME DEFAULT '17:00'
);

-- Login credentials live here, not on doctors/patients directly, so both
-- roles share one login table. `linked_id` points at patients.id or
-- doctors.id depending on `role`. See agent_service/auth.py (bcrypt) and
-- agent_service/users_store.py.
ALTER TABLE doctors DROP COLUMN IF EXISTS password_hash;

-- Self-heal older databases created before `email UNIQUE` existed on this
-- table — db/seed.sql's `ON CONFLICT (email)` upsert depends on it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'doctors_email_key'
    ) THEN
        ALTER TABLE doctors ADD CONSTRAINT doctors_email_key UNIQUE (email);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor')),
    linked_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patients (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT
);

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    doctor_id INT REFERENCES doctors(id),
    patient_id INT REFERENCES patients(id),
    scheduled_at TIMESTAMP NOT NULL,
    duration_minutes INT DEFAULT 30,
    status TEXT DEFAULT 'confirmed', -- confirmed | cancelled | completed
    reason TEXT,                      -- e.g. "fever", "checkup"
    google_calendar_event_id TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    role TEXT,              -- 'patient' or 'doctor'
    user_ref INT,            -- patient_id or doctor_id
    title TEXT,               -- auto-generated from the first user message
    history JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT now()
);

ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS title TEXT;

-- Flat, append-only log of every user-typed prompt (patient or doctor),
-- separate from conversation_sessions.history so read-only history browsing
-- doesn't need to parse the LLM message array (which isn't per-message
-- timestamped and is fed directly to the Groq SDK as-is).
CREATE TABLE IF NOT EXISTS prompt_log (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL,      -- 'patient' or 'doctor'
    user_ref INT NOT NULL,   -- patient_id or doctor_id
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    doctor_id INT REFERENCES doctors(id),
    message TEXT,
    channel TEXT,            -- 'in_app' | 'slack'
    created_at TIMESTAMP DEFAULT now(),
    read BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_appointments_doctor_scheduled ON appointments(doctor_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_notifications_doctor ON notifications(doctor_id, read);
CREATE INDEX IF NOT EXISTS idx_conversation_sessions_role_ref ON conversation_sessions(role, user_ref);
CREATE INDEX IF NOT EXISTS idx_prompt_log_role_ref ON prompt_log(role, user_ref, created_at DESC);
