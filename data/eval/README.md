# Checked posts for comparing models

- `labels.proposed.jsonl`: items with the label proposed by whoever wrote them. Reddit-style items are written for the set and marked `synthetic: true`. All current items are synthetic and their labels are proposed, not yet checked by a person.
- `labels.jsonl`: items a person has checked. `overheard eval` uses this file when it exists, else the proposed one, and says which in its output.
- Hacker News items are stored by id (`hn_id`) and fetched when the eval runs; `--synthetic-only` skips them.
- `results/<date>-<model>.json`: one file per eval run. Numbers only ever come from runs.

Products the items refer to (`product`) are the made-up samples: Mealwise, Shipnote and Plantpal; PlateUp is a made-up competitor.
