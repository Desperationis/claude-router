## Claude Router Enforcement

When you receive a `[Claude Router] MANDATORY ROUTING DIRECTIVE`, you MUST:

1. Use the Task tool IMMEDIATELY to spawn the specified subagent
2. Do NOT respond to the user directly - delegate to the subagent
3. Pass the user's original query in the prompt parameter

Subagent mapping:
- fast → `claude-router:fast-executor`
- standard → `claude-router:standard-executor`
- deep → `claude-router:deep-executor`

Exceptions: Slash commands (`/route`, `/router-stats`, `/router-stats-reset`, `/orchestrate`, `/router-analytics`, `/retry`) and questions about the router itself.

## Version Management

**IMPORTANT**: On EVERY `/commit`, you MUST bump the version number in `.claude-plugin/plugin.json` before committing.

- The version follows semver format (e.g., `2.0.7`)
- Increment the **patch** version (last number) for regular commits
- Increment the **minor** version (middle number) for new features, reset patch to 0
- Increment the **major** version (first number) for breaking changes, reset minor and patch to 0

Example: If current version is `2.0.7`, a regular commit should bump it to `2.0.8`.
