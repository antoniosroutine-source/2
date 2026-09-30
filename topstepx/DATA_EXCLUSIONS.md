# Data exclusions

Entries in `decisions.jsonl` to drop before any analysis.

| Date (trader's local time) | Window | What | Why |
|---|---|---|---|
| 2026-09-30 | 16:08:29 – 16:09:02 | all `my_setup` marks | clicked by accident while learning the desk page |

The desk page shows times in the computer's local clock. The trader's TopstepX shows UTC-7 (Pacific),
so 16:08 local is about 19:08 New York time. Match on the `ts` field (unix seconds) inside that window.
