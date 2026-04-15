---
name: router-stats
description: Display Claude Router usage statistics and cost savings
user_invokable: true
---

# Router Stats

Display usage statistics and estimated cost savings from Claude Router.

## Instructions

Read the stats file at `~/.claude/router-stats.json` and present the data in a clear, formatted way.

## Data Format

The stats file contains (v1.2 schema):
```json
{
  "version": "1.2",
  "total_queries": 100,
  "routes": {"fast": 30, "standard": 50, "deep": 10, "orchestrated": 10},
  "exceptions": {"router_meta": 2, "slash_commands": 3, "explicit_route": 1, "explicit_retry": 1},
  "tool_intensive_queries": 25,
  "orchestrated_queries": 10,
  "estimated_savings": 12.50,
  "delegation_savings": 2.50,
  "sessions": [
    {
      "date": "2026-01-03",
      "queries": 25,
      "routes": {"fast": 8, "standard": 12, "deep": 2, "orchestrated": 3},
      "savings": 3.20
    }
  ],
  "last_updated": "2026-01-03T15:30:00"
}
```

## Output Format

Present the stats like this:

```
╔═══════════════════════════════════════════════════╗
║           Claude Router Statistics                 ║
╚═══════════════════════════════════════════════════╝

📊 All Time
───────────────────────────────────────────────────
Total Queries Routed: 100

Route Distribution:
  Fast (Haiku):       30 (30%)  ████████░░░░░░░░░░░░
  Standard (Sonnet):  50 (50%)  ██████████████░░░░░░
  Deep (Opus):        10 (10%)  ████░░░░░░░░░░░░░░░░
  Orchestrated:       10 (10%)  ████░░░░░░░░░░░░░░░░

🔧 Tool-Aware Routing
───────────────────────────────────────────────────
Tool-Intensive Queries: 25 (25%)
Orchestrated Queries:   10 (10%)

⚡ Exceptions (tracked separately, not in route counts)
───────────────────────────────────────────────────
Slash Commands:   3  (e.g., /router-stats)
Explicit Routes:  1  (e.g., /route opus ...)
Explicit Retries: 1  (e.g., /retry sonnet)
Router Meta:      2  (queries about the router)
Total Exceptions: 7

💰 Cost Savings
───────────────────────────────────────────────────
Estimated Savings:   $12.50  (compared to always using Opus)
Delegation Savings:  $2.50   (from hybrid delegation)
Total Savings:       $15.00

📅 Today (2026-01-03)
───────────────────────────────────────────────────
Queries: 25
Savings: $3.20

Route Distribution:
  Fast: 8 | Standard: 12 | Deep: 2 | Orchestrated: 3
```

## Steps

1. Use the Read tool to read `~/.claude/router-stats.json`
2. If the file doesn't exist, inform the user that no stats are available yet
3. Calculate percentages for route distribution
4. Display exception counts if present (router_meta queries are handled by Opus despite classification)
5. Format and display the statistics
6. Include the savings comparison explanation

## Notes

- Savings are calculated assuming Opus would have been used for all queries
- Cost estimates use: Haiku 4.5 $1/$5, Sonnet 4.5 $3/$15, Opus 4.5 $5/$25 per 1M tokens
- Average query estimated at 1K input + 2K output tokens
- **Exceptions are tracked separately**: Slash commands, explicit model selections (/route, /retry), and router meta-queries are counted in `exceptions`, not in `routes`. This prevents double-counting.
- **No double counting**: `total_queries` = sum of routes + sum of exceptions. Route percentages and exception percentages should add up correctly.
