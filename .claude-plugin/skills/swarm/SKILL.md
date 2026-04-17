---
name: swarm
description: Launch 5-30 parallel agents with worktree isolation and auto-merge
user_invokable: true
---

# Swarm - Parallel Agent Coordinator

Launch a swarm of parallel agents to tackle complex tasks across your codebase.

## Usage

```
/swarm <task description>
```

## Instructions

**IMPORTANT: You (the main agent) orchestrate the swarm directly. Do NOT spawn a swarm-coordinator subagent — subagents cannot spawn further agents.**

When invoked:

1. **Read the orchestration instructions:**
   ```
   Read("agents/swarm-coordinator.md")
   ```

2. **Follow those instructions directly** to:
   - Decompose the task into 5-30 parallel subtasks (Phase 1)
   - Verify consistency (Phase 1.5)
   - Launch ALL worker agents in a SINGLE message (Phase 2)
   - Verify launch (Phase 2.5)
   - Monitor results and retry failures (Phase 3)
   - Merge worktree changes (Phase 4)
   - Synthesize final report (Phase 5)

3. **Spawn worker agents using:**
   ```
   Agent(
     description="<task summary>",
     prompt="<full task instructions>",
     subagent_type="claude-router:fast-executor",  # or standard-executor or deep-executor
     isolation="worktree"
   )
   ```

The three executor types and when to use them:
- `claude-router:fast-executor` — read-only tasks OR trivial single-file mechanical edits
- `claude-router:standard-executor` — real implementation work within one file
- `claude-router:deep-executor` — cross-file reasoning, security, architecture

## What Makes a Good Swarm Task

**Good candidates:**
- "Refactor all API handlers to use the new error pattern"
- "Add unit tests for every utility function"
- "Migrate database queries from raw SQL to ORM"
- "Review all files in src/ for security issues"
- "Update all React components to use hooks"

**Poor candidates:**
- Single-file changes (just use Sonnet directly)
- Sequential tasks where order matters
- Tasks requiring shared state between agents

## Model Mix

The coordinator assigns models based on subtask complexity:
- **Haiku** — file reads, searches, listings
- **Sonnet** — implementations, refactors, tests
- **Opus** — security analysis, architecture decisions

## Output

You'll see:
1. Live updates as agents complete their tasks
2. Any retry attempts for failed agents
3. Merge status as changes are combined
4. Final summary with success/failure counts

## Examples

```
/swarm Add comprehensive input validation to all API endpoints in src/api/

/swarm Migrate all class components in src/components/ to functional components with hooks

/swarm Audit the entire codebase for SQL injection vulnerabilities and fix any found
```
