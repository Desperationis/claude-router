#!/usr/bin/env python3
"""
Claude Router - Stop Hook: verify-routing

Backstop enforcement for the MANDATORY ROUTING DIRECTIVE.

When the UserPromptSubmit hook (classify-prompt.py) emits a routing directive,
the main agent is expected to invoke Task(subagent_type="claude-router:*-executor").
If the main agent ignores the directive and answers the user directly, this Stop
hook fires after the response completes, detects the violation, and returns
decision: "block" with a corrective reason. Claude Code will then resume the
turn and the main agent gets another chance to honor the directive.

Flow:
  1. Stop hook receives {transcript_path, stop_hook_active, ...} on stdin.
  2. If stop_hook_active is true, exit 0 immediately (we already blocked once
     in this continuation - do not loop).
  3. Parse the transcript JSONL. Find the last real user prompt (role=user,
     content is NOT a tool_result).
  4. From that point forward:
       a) Look for a UserPromptSubmit hook attachment carrying a routing
          directive (the "[Claude Router] MANDATORY ROUTING DIRECTIVE" or
          "[Claude Router] EXPLICIT ..." marker).
       b) Look for an assistant tool_use whose name == "Task" and whose
          input.subagent_type starts with "claude-router:" (ignoring case
          and minor drift - we accept any claude-router:* executor).
  5. Exceptions: if the directive text contains "Exception: router_meta" we
     SKIP enforcement - those queries are allowed to be answered directly.
  6. If a directive was emitted AND no matching Task call was issued, block
     with a corrective reason that tells the agent to retry and invoke Task.

The hook MUST fail-open: any unexpected error exits 0 so we never wedge the
user's session.

Part of claude-router: https://github.com/Desperationis/claude-router
"""
import json
import re
import sys
from pathlib import Path


DIRECTIVE_MARKERS = (
    "[Claude Router] MANDATORY ROUTING DIRECTIVE",
    "[Claude Router] EXPLICIT MODEL OVERRIDE",
    "[Claude Router] EXPLICIT RETRY OVERRIDE",
)

# If the directive text contains this marker, skip enforcement.
# The classifier tags router meta-questions with "| Exception: router_meta"
# so they can still be routed for stats but never forced through the Stop gate.
EXCEPTION_SKIP_MARKER = "Exception: router_meta"

# Canonical subagent prefix - any Task call with this prefix satisfies the
# directive. We accept variants (fast-executor, standard-executor,
# deep-executor, swarm-coordinator) because validate-swarm-task.py already
# normalizes the tail.
SUBAGENT_PREFIX = "claude-router:"

# Regex to extract the target subagent_type from the directive so the block
# message can echo it back to the agent.
DIRECTIVE_SUBAGENT_RE = re.compile(
    r'claude-router:(?:fast|standard|deep)-executor', re.IGNORECASE
)


def load_transcript(path_str: str) -> list:
    """Load all JSONL entries from the transcript file.

    Returns [] on any error (missing file, parse failures, etc.). Individual
    unparseable lines are skipped silently.
    """
    try:
        path = Path(path_str)
        if not path.exists() or not path.is_file():
            return []
        entries = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries
    except Exception:
        return []


def find_last_user_prompt_index(entries: list) -> int:
    """Return the index of the last real user-prompt entry.

    A real user prompt is:
      - type == "user"
      - message.content is either a plain string, OR a list whose items are
        NOT tool_result entries.

    Tool results masquerade as user messages in the transcript (role=user with
    content=[{type:"tool_result", ...}]) - we must skip those.

    Returns -1 if no user prompt is found.
    """
    for idx in range(len(entries) - 1, -1, -1):
        entry = entries[idx]
        if entry.get("type") != "user":
            continue

        msg = entry.get("message") or {}
        content = msg.get("content")

        if isinstance(content, str):
            # Plain string content = real user prompt (including slash cmds).
            return idx

        if isinstance(content, list):
            # A user message whose content list contains ANY tool_result is a
            # tool-result envelope, not a real user prompt.
            has_tool_result = any(
                isinstance(part, dict) and part.get("type") == "tool_result"
                for part in content
            )
            if not has_tool_result:
                return idx

    return -1


def extract_attachment_directive(entry: dict) -> str | None:
    """If entry is a UserPromptSubmit hook_additional_context attachment whose
    content includes a routing directive, return the directive text. Otherwise
    return None.
    """
    if entry.get("type") != "attachment":
        return None
    attachment = entry.get("attachment") or {}
    if attachment.get("type") != "hook_additional_context":
        return None
    if attachment.get("hookEvent") != "UserPromptSubmit" and \
       attachment.get("hookName") != "UserPromptSubmit":
        return None

    content = attachment.get("content")
    # content is usually a list of strings (one per hook invocation)
    if isinstance(content, str):
        content_list = [content]
    elif isinstance(content, list):
        content_list = [c for c in content if isinstance(c, str)]
    else:
        return None

    for block in content_list:
        for marker in DIRECTIVE_MARKERS:
            if marker in block:
                return block
    return None


# Claude Code has used both "Task" and "Agent" as the tool name for spawning
# subagents across versions. Accept either — the discriminator is the
# subagent_type field, not the tool name.
SUBAGENT_TOOL_NAMES = {"Task", "Agent"}


def find_task_call(entry: dict) -> str | None:
    """If entry is an assistant message with a claude-router subagent tool_use,
    return the subagent_type. Otherwise return None.
    """
    if entry.get("type") != "assistant":
        return None
    msg = entry.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return None

    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool_use":
            continue
        if part.get("name") not in SUBAGENT_TOOL_NAMES:
            continue
        tool_input = part.get("input") or {}
        st = tool_input.get("subagent_type")
        if isinstance(st, str) and st.strip().lower().startswith(SUBAGENT_PREFIX):
            return st
    return None


def build_block_reason(directive_text: str) -> str:
    """Craft a corrective reason that quotes the directive back at the agent."""
    # Try to pull the subagent name out of the directive so the reason is
    # concrete instead of hand-wavy.
    match = DIRECTIVE_SUBAGENT_RE.search(directive_text)
    target = match.group(0) if match else "claude-router:<executor>"

    return (
        f"You ignored the MANDATORY ROUTING DIRECTIVE. The UserPromptSubmit "
        f"hook instructed you to delegate via Task(subagent_type=\"{target}\"), "
        f"but you answered directly instead. Invoke the Task tool NOW with "
        f"subagent_type=\"{target}\", passing the user's original query as the "
        f"prompt parameter. Return only the subagent's output. Do not answer "
        f"the user directly. This is enforced by the claude-router Stop hook."
    )


def main():
    # Fail-open wrapper so the hook never wedges the session.
    try:
        _main_inner()
    except Exception as e:
        print(f"[claude-router] verify-routing crashed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)


def _main_inner():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    # Loop guard: if we already blocked once and the agent is now continuing,
    # let it finish this time. Prevents infinite Stop->block->Stop->block loops
    # if the agent still refuses to comply.
    if payload.get("stop_hook_active") is True:
        sys.exit(0)

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        sys.exit(0)

    entries = load_transcript(transcript_path)
    if not entries:
        sys.exit(0)

    last_user_idx = find_last_user_prompt_index(entries)
    if last_user_idx < 0:
        sys.exit(0)

    # Scan from the last user prompt forward.
    directive_text = None
    task_subagent = None
    for entry in entries[last_user_idx + 1:]:
        if directive_text is None:
            d = extract_attachment_directive(entry)
            if d:
                directive_text = d
                continue
        if task_subagent is None:
            s = find_task_call(entry)
            if s:
                task_subagent = s

    # No directive was emitted this turn - nothing to enforce.
    if directive_text is None:
        sys.exit(0)

    # Router-meta queries are explicitly exempt from Stop enforcement.
    if EXCEPTION_SKIP_MARKER in directive_text:
        sys.exit(0)

    # Directive was emitted AND a matching Task call happened - all good.
    if task_subagent is not None:
        sys.exit(0)

    # Violation: directive was emitted but no Task(claude-router:*) call.
    # Block the stop and force the agent to comply.
    reason = build_block_reason(directive_text)
    output = {
        "decision": "block",
        "reason": reason,
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
