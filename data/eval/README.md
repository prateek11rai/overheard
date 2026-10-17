# Checked posts for comparing models

- `labels.proposed.jsonl`: items with a proposed label. **The labels are proposed, not yet checked by a person.**
  - 30 Reddit-style items written for the set (`synthetic: true`) about the made-up samples Mealwise, Shipnote and Plantpal (PlateUp is a made-up competitor).
  - 60 Hacker News items stored **by id only** (`hn_id`): no title, text or username is kept here. `overheard eval` fetches each one live from the Hacker News API (at most one request a second) and leaves out items that were deleted since. They are about two real products, with the descriptions and competitors the eval gives the model: Supabase (competitor Firebase) and Obsidian (competitors Notion and Logseq).
- `labels.jsonl`: items a person has checked. `overheard eval` uses this file when it exists, else the proposed one, and says which in its output and in Settings › Compare models.
- `results/<date>-<model>.json`: one file per eval run (labels by item id, no post text). Numbers only ever come from runs.

How the Hacker News labels were proposed (one label per item, the first that fits):

1. **ask**: asks for a tool that does what the product does.
2. **you**: names the product, even in passing or while comparing it.
3. **rival**: names a listed competitor but not the product.
4. **pain**: describes the problem the product solves without naming either.
5. **noise**: anything else, including posts that only share words with the topic (auth, storage, notes).

Hacker News rarely has posts asking for a tool like these two products, so ask recall still comes from the synthetic items.
