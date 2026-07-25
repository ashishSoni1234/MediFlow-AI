-- MediFlow-AI schema
-- Run with: psql -U <user> -d <db> -f db/schema.sql

CREATE TABLE IF NOT EXISTS doctors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    specialty TEXT,
    email TEXT,
    working_hours_start TIME DEFAULT '09:00',
    working_hours_end TIME DEFAULT '17:00'
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
    history JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT now()
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
