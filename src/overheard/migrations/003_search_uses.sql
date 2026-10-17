CREATE TABLE search_uses (
    run_key text NOT NULL,
    idempotency_key text NOT NULL REFERENCES search_ledger (idempotency_key) ON DELETE CASCADE,
    used_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_key, idempotency_key)
);
INSERT INTO search_uses (run_key, idempotency_key)
SELECT run_key, idempotency_key FROM search_ledger ON CONFLICT DO NOTHING;
