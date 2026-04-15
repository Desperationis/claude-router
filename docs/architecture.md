# Project Architecture

## Project Structure

```
claude-router/
├── .claude-plugin/                # Plugin files (marketplace distribution)
│   ├── agents/
│   │   ├── fast-executor.md       # Haiku agent
│   │   ├── standard-executor.md   # Sonnet agent
│   │   ├── deep-executor.md       # Opus agent
│   │   └── swarm-coordinator.md   # Swarm coordinator (v2.2)
│   ├── commands/                  # Slash command definitions
│   │   ├── route.md
│   │   ├── router-stats.md
│   │   ├── router-stats-reset.md  # (v2.1)
│   │   ├── swarm.md               # (v2.2)
│   │   ├── router-analytics.md    # (v2.0)
│   │   └── retry.md               # (v2.0)
│   ├── hooks/
│   │   └── classify-prompt.py     # Hybrid classifier with multi-turn awareness
│   ├── skills/
│   │   ├── route/                 # Manual routing skill
│   │   ├── router-stats/          # Statistics skill
│   │   ├── router-stats-reset/    # Reset statistics (v2.1)
│   │   ├── swarm/                 # Parallel agents (v2.2)
│   │   ├── router-analytics/      # HTML dashboard (v2.0)
│   │   └── retry/                 # Error recovery (v2.0)
│   └── plugin.json                # Plugin manifest
├── agents/                        # Source agent definitions
├── commands/                      # Source command definitions
├── hooks/                         # Source hook scripts
├── skills/                        # Source skills
└── docs/                          # Documentation
```

---

## Core Components

### UserPromptSubmit Hook (`hooks/classify-prompt.py`)

The heart of Claude Router. This hook:
1. Intercepts every user query before Claude processes it
2. Classifies the query using rule-based patterns (+ optional Haiku LLM fallback)
3. Injects a routing directive that triggers the appropriate subagent

**Key features (v2.0):**
- Pre-compiled regex patterns for speed
- Session state tracking for multi-turn awareness
- Follow-up query detection

### Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| `fast-executor` | Haiku | Simple queries, lookups, formatting |
| `standard-executor` | Sonnet | Typical coding tasks, tool-intensive work |
| `deep-executor` | Opus | Complex architecture, security, trade-offs |
| `swarm-coordinator` | Opus | Parallel agent decomposition and coordination |

### Skills

Skills implement the actual functionality behind slash commands:

| Skill | Command | Description |
|-------|---------|-------------|
| `route` | `/route` | Manual model override |
| `router-stats` | `/router-stats` | Usage statistics |
| `router-stats-reset` | `/router-stats-reset` | Reset statistics |
| `swarm` | `/swarm` | Parallel agent execution |
| `router-analytics` | `/router-analytics` | HTML dashboard |
| `retry` | `/retry` | Error recovery |

---

## Data Flow

```
User Query
    │
    ▼
┌─────────────────────────────────┐
│  UserPromptSubmit Hook          │
│  (classify-prompt.py)           │
├─────────────────────────────────┤
│  1. Rule-based classification   │
│  2. Context boost (follow-ups)  │
│  3. LLM fallback (if needed)    │
│  4. Session state update        │
│  5. Inject routing directive    │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│  Claude Code (Main)             │
│  Sees: [Claude Router] MANDATORY│
│  ROUTING DIRECTIVE              │
├─────────────────────────────────┤
│  Spawns appropriate subagent    │
│  via Task tool                  │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│  Subagent (Haiku/Sonnet/Opus)   │
│  Executes the actual task       │
└─────────────────────────────────┘
    │
    ▼
Response to User
```

---

## State Files

### `~/.claude/router-stats.json`
Global routing statistics across all projects.

### `~/.claude/router-session.json`
Session state for multi-turn context awareness.
