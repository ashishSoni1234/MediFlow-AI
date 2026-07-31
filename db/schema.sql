-- ============================================================================
-- MediFlow-AI Database Schema
-- Run with: psql -U <user> -d <db> -f db/schema.sql
-- This schema defines all core tables required for authentication,
-- appointment scheduling, conversations, notifications, and prompt logging.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Doctors Table
-- Stores doctor profile information and working schedule.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doctors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    specialty TEXT,
    email TEXT UNIQUE,
    -- Default clinic start time
    working_hours_start TIME DEFAULT '09:00',
    -- Default clinic end time
    working_hours_end TIME DEFAULT '17:00'
);

-- ----------------------------------------------------------------------------
-- Authentication is centralized inside the users table.
-- Password hashes are intentionally removed from doctors so both doctors
-- and patients share a common authentication mechanism.
--
-- linked_id references either:
--   - patients.id
--   - doctors.id
-- depending on the user's role.
--
-- Authentication implementation:
-- agent_service/auth.py
-- agent_service/users_store.py
-- ----------------------------------------------------------------------------
ALTER TABLE doctors DROP COLUMN IF EXISTS password_hash;

-- ----------------------------------------------------------------------------
-- Backward compatibility migration.
--
-- Older databases may not contain a UNIQUE constraint on doctor email.
-- The seed script relies on ON CONFLICT(email), therefore we ensure the
-- constraint exists before inserting seed data.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'doctors_email_key'
    ) THEN
        ALTER TABLE doctors ADD CONSTRAINT doctors_email_key UNIQUE (email);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Users Table
--
-- Central authentication table for every login.
-- Stores encrypted passwords instead of plain text credentials.
--
-- role:
--   patient
--   doctor
--
-- linked_id maps this user account to either doctors.id or patients.id.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor')),
    linked_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- Patients Table
-- Stores patient profile and contact information.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patients (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT
);

-- ----------------------------------------------------------------------------
-- Appointments Table
--
-- Maintains appointment scheduling between doctors and patients.
--
-- status values:
--   confirmed
--   cancelled
--   completed
--
-- google_calendar_event_id stores the external calendar event ID when
-- Google Calendar integration is enabled.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    doctor_id INT REFERENCES doctors(id),
    patient_id INT REFERENCES patients(id),
    scheduled_at TIMESTAMP NOT NULL,
    duration_minutes INT DEFAULT 30,
    status TEXT DEFAULT 'confirmed', -- confirmed | cancelled | completed
    reason TEXT,                     -- Example: fever, consultation, checkup
    google_calendar_event_id TEXT,
    created_at TIMESTAMP DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- Conversation Sessions
--
-- Stores complete chat history between users and the AI assistant.
--
-- history:
-- JSONB array containing serialized chat messages.
--
-- title:
-- Automatically generated from the user's first prompt.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    role TEXT,               -- patient | doctor
    user_ref INT,            -- references patient_id or doctor_id
    title TEXT,              -- auto-generated conversation title
    history JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT now()
);

-- Ensure title exists when upgrading from older schema versions.
ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS title TEXT;

-- ----------------------------------------------------------------------------
-- Prompt Log
--
-- Stores every user prompt independently from conversation history.
--
-- Benefits:
--   • Faster analytics
--   • Individual prompt timestamps
--   • Audit trail
--   • Read-only history retrieval without parsing conversation JSON
--
-- This table intentionally keeps an append-only log.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompt_log (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL,      -- patient | doctor
    user_ref INT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- Notifications
--
-- Stores notifications delivered to doctors.
--
-- channel values:
--   in_app
--   slack
--
-- read indicates whether the notification has been viewed.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    doctor_id INT REFERENCES doctors(id),
    message TEXT,
    channel TEXT,            -- in_app | slack
    created_at TIMESTAMP DEFAULT now(),
    read BOOLEAN DEFAULT false
);

-- ============================================================================
-- Performance Indexes
-- These indexes improve query performance for frequently accessed data.
-- ============================================================================

-- Quickly fetch appointments for a doctor ordered by scheduled time.
CREATE INDEX IF NOT EXISTS idx_appointments_doctor_scheduled
ON appointments(doctor_id, scheduled_at);

-- Speeds up filtering appointments by status.
CREATE INDEX IF NOT EXISTS idx_appointments_status
ON appointments(status);

-- Optimizes unread notification lookup for doctors.
CREATE INDEX IF NOT EXISTS idx_notifications_doctor
ON notifications(doctor_id, read);

-- Improves retrieval of conversation sessions for a specific user.
CREATE INDEX IF NOT EXISTS idx_conversation_sessions_role_ref
ON conversation_sessions(role, user_ref);

-- Accelerates prompt history queries sorted by latest activity.
CREATE INDEX IF NOT EXISTS idx_prompt_log_role_ref
ON prompt_log(role, user_ref, created_at DESC);
