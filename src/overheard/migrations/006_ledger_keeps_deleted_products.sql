ALTER TABLE search_ledger DROP CONSTRAINT search_ledger_product_id_fkey;
ALTER TABLE search_ledger ADD CONSTRAINT search_ledger_product_id_fkey
    FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE SET NULL;
