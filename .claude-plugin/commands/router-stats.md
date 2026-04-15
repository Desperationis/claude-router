---
name: router-stats
description: Display Claude Router usage statistics and cost savings (global across all projects)
---

# /router-stats Command

Display usage statistics and estimated cost savings from Claude Router.

**Note:** Stats are global - they track routing across all your projects.

## Usage

```
/router-stats
```

## Instructions

1. Read the stats file at `~/.claude/router-stats.json`
2. If the file doesn't exist, inform the user that no stats are available yet
3. Calculate percentages for route distribution
4. Calculate **optimization rate**: percentage of queries routed to Haiku or Sonnet instead of Opus
5. Format and display the statistics

## Data Format

The stats file contains (v1.2 schema):
```json
{
  "version": "1.2",
  "total_queries": 100,
  "routes": {"fast": 30, "standard": 50, "deep": 8, "complex": 5},
  "exceptions": {"router_meta": 2, "slash_commands": 3, "explicit_route": 1, "explicit_retry": 1},
  "tool_intensive_queries": 15,
  "complex_queries": 5,
  "estimated_savings": 12.50,
  "delegation_savings": 2.00,
  "sessions": [
    {
      "date": "2026-01-03",
      "queries": 25,
      "routes": {"fast": 8, "standard": 12, "deep": 2, "complex": 3},
      "savings": 3.20
    }
  ],
  "last_updated": "2026-01-03T15:30:00"
}
```

## Output Format

Present the stats like this:

```
Claude Router Statistics (Global)
==================================

All Time
--------
Total Queries Routed: 100
Optimization Rate: 90% (queries routed to cheaper models)

Route Distribution:
  Fast (Haiku):      30 (32%)
  Standard (Sonnet): 50 (54%)
  Deep (Opus):        8 ( 9%)
  Complex:       5 ( 5%)

Exceptions (not counted in routes):
  Slash Commands:  3
  Explicit Route:  1
  Explicit Retry:  1
  Router Meta:     2

Value Metrics:
  Estimated Savings: $12.50 (vs always using Opus)
  Delegation Savings: $2.00 (from /swarm)
  Estimated Tokens Saved: ~2.7M tokens
  Avg Cost per Query: $0.02 (vs $0.055 with Opus 4.5)

Today
-----
Queries: 25
Savings: $3.20
Routes: Fast 8 | Standard 12 | Deep 2 | Complex 3
```

**Note on counting:**
- `routes` only counts actual routing decisions (fast/standard/deep/complex)
- `exceptions` tracks queries handled separately (slash commands, explicit model selection, router meta-questions)
- `total_queries` = sum of routes + sum of exceptions (no double counting)

## Why This Matters for Subscribers

If you're on Claude Pro or Max, these metrics translate to real benefits:

- **Extended usage limits** - Routing to smaller models uses less of your monthly capacity
- **Longer sessions** - Less context consumed means fewer auto-compacts
- **Faster responses** - Haiku responds 3-5x faster than Opus

## Metrics Explained

- **Optimization Rate**: Percentage of queries routed to Haiku or Sonnet instead of Opus
- **Estimated Savings**: Total cost saved compared to always using Opus (API users)
- **Estimated Tokens Saved**: Approximate tokens conserved by using efficient models
- **Avg Cost per Query**: Your actual average cost vs what Opus would cost

## Cost Reference

Model pricing per 1M tokens (input/output):
- Haiku 4.5: $1 / $5
- Sonnet 4.5: $3 / $15
- Opus 4.5: $5 / $25

Average query estimated at 1K input + 2K output tokens (~$0.055 with Opus 4.5).
