---
name: router-stats-reset
description: Reset Claude Router usage statistics (deletes ~/.claude/router-stats.json)
---

# /router-stats-reset Command

Reset Claude Router statistics by deleting the stats file at `~/.claude/router-stats.json`.

**Warning:** This is destructive. All historical routing counts, cost savings, and session history will be lost. There is no undo.

## Usage

```
/router-stats-reset
```

## Instructions

1. Check whether `~/.claude/router-stats.json` exists
2. If it does not exist, inform the user that there are no stats to reset
3. If it exists, delete the file
4. Confirm to the user that stats were reset successfully
5. Let the user know that the next routed query will recreate the file from scratch

## Example Output (stats existed)

```
Claude Router Statistics Reset
==============================

Deleted: ~/.claude/router-stats.json

All routing counters, cost savings, and session history have been cleared.
The stats file will be recreated automatically on the next routed query.
```

## Example Output (no stats existed)

```
Claude Router Statistics Reset
==============================

No stats file found at ~/.claude/router-stats.json.
Nothing to reset - statistics are already empty.
```

## Notes

- Stats are global across all projects, so resetting affects every project you use Claude Router in
- The underlying hook (`hooks/classify-prompt.py`) will recreate the file with the current schema version on the next query
- Use `/router-stats` afterward to verify the reset (it should report no stats available yet)
