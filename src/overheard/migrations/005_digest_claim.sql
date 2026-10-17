ALTER TABLE digest_ledger ADD COLUMN status text NOT NULL DEFAULT 'sent'
    CHECK (status IN ('sending', 'sent'));
