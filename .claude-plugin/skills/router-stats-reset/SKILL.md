---
name: router-stats-reset
description: Reset Claude Router usage statistics by deleting ~/.claude/router-stats.json
user_invokable: true
---

# Router Stats Reset

Reset Claude Router statistics by deleting the global stats file at `~/.claude/router-stats.json`.

This is useful when you want to start fresh - for example, after a long benchmarking session, at the start of a new month, or after changing routing rules and wanting a clean baseline.

**Warning:** This is a destructive operation. All historical routing counts, cost savings, and session history will be permanently lost. There is no undo.

## Instructions

1. Use the Bash tool to check whether `~/.claude/router-stats.json` exists
2. If it does not exist, inform the user that there are no stats to reset and stop
3. If it exists, delete the file using `rm ~/.claude/router-stats.json`
4. Confirm to the user that stats were reset successfully
5. Mention that the stats file will be recreated automatically on the next routed query

## Steps

1. Run `test -f ~/.claude/router-stats.json` to check existence
2. If the file exists:
   - Run `rm ~/.claude/router-stats.json`
   - Report successful deletion
3. If the file does not exist:
   - Report that nothing needed to be reset
4. Do not create an empty stats file - the hook will recreate it with the correct schema on the next query

## Output Format (stats existed)

```
╔═══════════════════════════════════════════════════╗
║        Claude Router Statistics Reset             ║
╚═══════════════════════════════════════════════════╝

Deleted: ~/.claude/router-stats.json

All routing counters, cost savings, and session history
have been cleared. The stats file will be recreated
automatically on the next routed query.

Run /router-stats to confirm.
```

## Output Format (no stats existed)

```
╔═══════════════════════════════════════════════════╗
║        Claude Router Statistics Reset             ║
╚═══════════════════════════════════════════════════╝

No stats file found at ~/.claude/router-stats.json.
Nothing to reset - statistics are already empty.
```

## Notes

- Stats are global across all projects on this machine, so resetting affects every project that uses Claude Router
- The underlying hook (`hooks/classify-prompt.py`) recreates the file with the current schema version on the next query
- Do not write an empty or partial JSON file - always delete, never truncate, so the hook can initialize with the latest schema
- If the user seems unsure, suggest they run `/router-stats` first to see what will be lost before resetting
