-- One row per (run, product) digest that was sent or saved, so a retried send_digest never
-- sends the same morning email twice.
CREATE TABLE digest_ledger (
    idempotency_key text PRIMARY KEY,
    run_key text NOT NULL,
    product_id bigint REFERENCES products (id) ON DELETE CASCADE,
    outcome text NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now()
);
