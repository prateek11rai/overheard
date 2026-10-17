-- Runs with search_path set to the app schema first, then public (where pgvector lives).

CREATE TABLE products (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    phrases text[] NOT NULL DEFAULT '{}',
    competitors text[] NOT NULL DEFAULT '{}',
    subreddits text[] NOT NULL DEFAULT '{}',
    sources text NOT NULL DEFAULT 'reddit+hn' CHECK (sources IN ('reddit+hn', 'hn')),
    recipients text[] NOT NULL DEFAULT '{}',
    quiet_day_email boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    demo boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE themes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    name text NOT NULL,
    embed_model text,
    centroid vector,
    first_seen timestamptz NOT NULL DEFAULT now(),
    post_count integer NOT NULL DEFAULT 0
);
CREATE INDEX themes_product_idx ON themes (product_id);

CREATE TABLE runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workflow_id text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('morning', 'check', 'reembed', 'compare')),
    product_id bigint REFERENCES products (id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'done', 'hn_only', 'failed')),
    note text NOT NULL DEFAULT '',
    reddit_planned integer NOT NULL DEFAULT 0,
    searches_used integer NOT NULL DEFAULT 0,
    new_posts integer NOT NULL DEFAULT 0,
    demo boolean NOT NULL DEFAULT false,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX runs_started_idx ON runs (started_at DESC);

CREATE TABLE run_steps (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    product_id bigint REFERENCES products (id) ON DELETE CASCADE,
    step_key text NOT NULL,
    text text NOT NULL,
    status text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'retried', 'warn', 'failed')),
    seconds real NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_key)
);

CREATE TABLE posts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    source text NOT NULL CHECK (source IN ('reddit', 'hn')),
    url text NOT NULL,
    title text NOT NULL,
    body text NOT NULL DEFAULT '',
    community text NOT NULL DEFAULT '',
    posted_at timestamptz,
    found_at timestamptz NOT NULL DEFAULT now(),
    run_id bigint REFERENCES runs (id) ON DELETE SET NULL,
    label text CHECK (label IN ('ask', 'pain', 'you', 'rival', 'noise', 'unverified')),
    quote text,
    competitor text,
    label_model text,
    label_attempts integer NOT NULL DEFAULT 0,
    reply_draft text,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'done', 'skipped')),
    snoozed_until timestamptz,
    theme_id bigint REFERENCES themes (id) ON DELETE SET NULL,
    search tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, ''))
    ) STORED,
    UNIQUE (product_id, url)
);
CREATE INDEX posts_product_found_idx ON posts (product_id, found_at DESC);
CREATE INDEX posts_theme_idx ON posts (theme_id);
CREATE INDEX posts_search_idx ON posts USING gin (search);

-- One HNSW index per embedding model is created by the app (db.ensure_embedding_index).
CREATE TABLE post_embeddings (
    post_id bigint NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    model text NOT NULL,
    dim integer NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, model)
);

CREATE TABLE search_ledger (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    run_key text NOT NULL,
    day date NOT NULL,
    product_id bigint REFERENCES products (id) ON DELETE CASCADE,
    phrase text NOT NULL,
    source text NOT NULL DEFAULT 'reddit' CHECK (source IN ('reddit', 'hn')),
    result_count integer NOT NULL DEFAULT 0,
    results jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX search_ledger_day_idx ON search_ledger (day);
CREATE INDEX search_ledger_phrase_idx ON search_ledger (product_id, phrase, day DESC);

CREATE TABLE settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_results (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('checked', 'recent')),
    model text NOT NULL,
    items integer NOT NULL,
    right_label integer NOT NULL,
    quote_ok integer NOT NULL,
    ask_total integer NOT NULL,
    ask_found integer NOT NULL,
    seconds_per_post real NOT NULL,
    labels_source text NOT NULL DEFAULT '',
    details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
