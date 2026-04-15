---
name: swarm
description: Launch 5-30 parallel agents with worktree isolation and auto-merge
---

# /swarm Command

Launch a parallel swarm of agents to tackle complex tasks with full isolation.

## Usage

```
/swarm <your task description>
```

## What It Does

1. **Opus Coordinator** analyzes your task and decomposes it into 5-30 subtasks, tagging each with a model
2. **Mixed-Model Swarm** — every subtask is explicitly tagged Haiku/Sonnet/Opus based on whether it reads, edits, or reasons. A healthy swarm is roughly 30% Haiku reads, 60% Sonnet edits, 10% Opus decisions — not a Sonnet monoculture
3. **Parallel Agents** spawn in isolated git worktrees (no conflicts)
4. **Live Streaming** — results appear as agents complete
5. **Auto-Merge** — all changes merge back to your starting branch
6. **Final Synthesis** — Opus summarizes what was accomplished

## Examples

```
/swarm Refactor all API handlers to use the new error handling pattern
/swarm Add unit tests for every function in src/utils/
/swarm Migrate all database queries from raw SQL to the ORM
/swarm Review all files in src/ for security vulnerabilities
```

## When to Use

- Tasks that naturally parallelize (per-file, per-module, per-feature)
- Large refactors across many files
- Comprehensive test coverage additions
- Codebase-wide audits or migrations

## Failure Handling

- Failed agents retry automatically
- Persistent failures don't block other agents
- Failures are reported in the final synthesis

## Cost Note

Swarms are cost-effective only when the coordinator actually uses the cheap models. The coordinator is instructed to tag every read-only subtask (grep, ls, scan, extract) as Haiku and reserve Opus for genuine reasoning. If you notice the coordinator over-spending on Sonnet for trivial reads, the model selection rules in `agents/swarm-coordinator.md` are where to tune it.
