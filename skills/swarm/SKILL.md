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

When invoked, immediately spawn the swarm-coordinator agent with the user's task:

```
Agent(
  description="Coordinate swarm for: <brief task summary>",
  prompt="$ARGUMENTS",
  subagent_type="claude-router:swarm-coordinator"
)
```

Note: Do NOT pass a `model` parameter. The swarm-coordinator's frontmatter already declares `model: claude-opus-4-5-20251101` as the single source of truth. Passing `model="opus"` here creates two sources of truth that can drift and cause the coordinator to run on the wrong model.

The swarm-coordinator will:
1. Decompose the task into 5-30 parallel subtasks
2. Launch all agents simultaneously in isolated worktrees
3. Stream results as agents complete
4. Retry failed agents automatically
5. Merge all changes back to the starting branch
6. Provide a final synthesis

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
