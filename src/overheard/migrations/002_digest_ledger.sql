CREATE TABLE digest_ledger (
    idempotency_key text PRIMARY KEY,
    run_key text NOT NULL,
    product_id bigint REFERENCES products (id) ON DELETE CASCADE,
    outcome text NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now()
);
