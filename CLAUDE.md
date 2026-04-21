## Claude Router Enforcement

When you receive a `[Claude Router] MANDATORY ROUTING DIRECTIVE`, you MUST:

1. Use the Task tool IMMEDIATELY to spawn the specified subagent
2. Do NOT respond to the user directly - delegate to the subagent
3. Build the `prompt` parameter using the **context-enriched template** from
   the directive: a `## Conversation context` section (your 1-3 sentence
   synthesis from the cached transcript), an optional `## Recent focus`
   bullet list, and a `## Current request` section containing the user's
   verbatim query. The subagent has NO MEMORY of this conversation — the
   context block is what gives it the situational awareness to do useful
   work. The `## Current request` section is sacred (verbatim user query,
   no edits); the context block above it is your synthesis.

Subagent mapping:
- fast → `claude-router:fast-executor`
- deep → `claude-router:deep-executor`
- standard → **passthrough** (parent model handles it; no directive emitted)

Passthrough behavior: when the classifier picks `standard` (Sonnet) — whether
auto-classified or via `/route sonnet` / `/retry sonnet` — the hook emits only
a `systemMessage` (e.g. `→ Sonnet (passthrough): ...`) and skips the routing
directive. The parent model is overwhelmingly Sonnet already, so spawning a
`standard-executor` would only add a Task hop without changing the model. Stats
and session state are still updated; the Stop hook (`verify-routing.py`) sees
no directive to enforce, so no Task call is required from you.

Exceptions: Slash commands (`/route`, `/router-stats`, `/router-stats-reset`, `/swarm`, `/router-analytics`, `/retry`) and questions about the router itself.

## Version Management

**IMPORTANT**: On EVERY `/commit`, you MUST bump the version number in `.claude-plugin/plugin.json` before committing.

- The version follows semver format (e.g., `2.0.7`)
- Increment the **patch** version (last number) for regular commits
- Increment the **minor** version (middle number) for new features, reset patch to 0
- Increment the **major** version (first number) for breaking changes, reset minor and patch to 0

Example: If current version is `2.0.7`, a regular commit should bump it to `2.0.8`.
