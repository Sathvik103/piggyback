-- Operator accounts and sign-in sessions.
--
-- Passwords are stored only as scrypt hashes (db/passwords.py). Session tokens are
-- random and live only in the browser's HttpOnly cookie; the database keeps their
-- SHA-256, so a leaked sessions table can't be replayed.
CREATE TABLE users (
    username      text PRIMARY KEY CHECK (username ~ '^[a-z0-9][a-z0-9._-]{1,31}$'),
    display_name  text NOT NULL,
    role          text NOT NULL CHECK (role IN ('operator', 'viewer')),
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    disabled      boolean NOT NULL DEFAULT false
);

CREATE TABLE sessions (
    token_sha256 bytea PRIMARY KEY,
    username     text NOT NULL REFERENCES users ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX sessions_by_user ON sessions (username);
