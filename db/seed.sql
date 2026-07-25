-- Seed data for MediFlow-AI demo
-- Run AFTER schema.sql: psql -U <user> -d <db> -f db/seed.sql
--
-- Appointment timestamps are computed relative to CURRENT_DATE so the
-- "yesterday" / "today" / "this week" sample prompts in the README work
-- correctly no matter when this script is run.

INSERT INTO doctors (name, specialty, email, working_hours_start, working_hours_end) VALUES
    ('Dr. Ahuja', 'General Physician', 'ahuja@mediflow.example', '09:00', '17:00'),
    ('Dr. Mehta', 'Pediatrics', 'mehta@mediflow.example', '10:00', '18:00'),
    ('Dr. Rao', 'Dermatology', 'rao@mediflow.example', '09:30', '16:30')
ON CONFLICT (email) DO NOTHING;

-- Demo login password for all three seeded doctors: "mediflow123"
-- (bcrypt — see agent_service/auth.py). Doctor signups are restricted to
-- these pre-seeded emails (agent_service/main.py's /auth/signup), so this
-- is what lets the demo doctors log in without going through signup.
INSERT INTO users (name, email, password_hash, role, linked_id)
SELECT d.name, d.email, '$2b$12$BXfh5Y7QkSAciJ1ytXWNP.oxiRcI/6f1VoWt5hOb/nQ5UXwbz15si', 'doctor', d.id
FROM doctors d
WHERE d.email IN ('ahuja@mediflow.example', 'mehta@mediflow.example', 'rao@mediflow.example')
ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash;

INSERT INTO patients (name, email, phone) VALUES
    ('Aarav Sharma', 'aarav.sharma@example.com', '9800000001'),
    ('Priya Nair', 'priya.nair@example.com', '9800000002'),
    ('Rohan Gupta', 'rohan.gupta@example.com', '9800000003'),
    ('Sneha Iyer', 'sneha.iyer@example.com', '9800000004'),
    ('Kabir Khan', 'kabir.khan@example.com', '9800000005')
ON CONFLICT DO NOTHING;

-- Appointments: mix of yesterday, today, tomorrow, and earlier this week,
-- with varied reasons/statuses so stats-and-filter prompts have data to hit.
INSERT INTO appointments (doctor_id, patient_id, scheduled_at, duration_minutes, status, reason) VALUES
    -- Dr. Ahuja (id 1)
    (1, 1, (CURRENT_DATE - INTERVAL '1 day') + TIME '09:30', 30, 'completed', 'fever'),
    (1, 2, (CURRENT_DATE - INTERVAL '1 day') + TIME '11:00', 30, 'completed', 'checkup'),
    (1, 3, CURRENT_DATE + TIME '10:00', 30, 'confirmed', 'fever'),
    (1, 4, CURRENT_DATE + TIME '15:00', 30, 'confirmed', 'follow-up'),
    (1, 5, (CURRENT_DATE + INTERVAL '1 day') + TIME '09:30', 30, 'confirmed', 'checkup'),

    -- Dr. Mehta (id 2)
    (2, 2, (CURRENT_DATE - INTERVAL '2 day') + TIME '10:30', 30, 'completed', 'vaccination'),
    (2, 4, (CURRENT_DATE - INTERVAL '1 day') + TIME '14:00', 30, 'completed', 'fever'),
    (2, 1, CURRENT_DATE + TIME '11:30', 30, 'confirmed', 'checkup'),
    (2, 5, (CURRENT_DATE + INTERVAL '1 day') + TIME '16:00', 30, 'confirmed', 'fever'),

    -- Dr. Rao (id 3)
    (3, 3, (CURRENT_DATE - INTERVAL '3 day') + TIME '09:30', 30, 'completed', 'rash'),
    (3, 1, CURRENT_DATE + TIME '13:00', 30, 'confirmed', 'checkup'),
    (3, 2, (CURRENT_DATE + INTERVAL '2 day') + TIME '10:00', 30, 'confirmed', 'rash');
