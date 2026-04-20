#!/usr/bin/env python3
"""
Claude Router - PreToolUse Hook: validate-swarm-task

Fires on Task tool calls to:
1. Validate `subagent_type` is one of the four valid executors
2. Normalize common drift patterns (e.g., "fast-executor" -> "claude-router:fast-executor")
3. Force the correct model for each subagent_type (single source of truth)
4. Block invalid subagent_types with a corrective message

This prevents the swarm-coordinator (or any other agent) from drifting off
the canonical four-executor set and from running subagents on the wrong model.

Part of claude-router: https://github.com/Desperationis/claude-router
"""
import json
import sys
import re

# Canonical mapping: subagent_type -> required model id
# This is the single source of truth for what model each executor runs on.
VALID_SUBAGENTS = {
    "claude-router:fast-executor":       "claude-haiku-4-5-20251001",
    "claude-router:standard-executor":   "claude-sonnet-4-5-20250929",
    "claude-router:deep-executor":       "claude-opus-4-7",
    "claude-router:swarm-coordinator":   "claude-opus-4-7",
}

# Drift normalization: common malformed values -> canonical value.
# Applied case-insensitively against a stripped/lowercased copy of the
# incoming subagent_type. The key is the *normalized* (lowercase, stripped)
# form; the value is the canonical string to substitute.
DRIFT_NORMALIZATION = {
    # missing prefix
    "fast-executor":                   "claude-router:fast-executor",
    "standard-executor":               "claude-router:standard-executor",
    "deep-executor":                   "claude-router:deep-executor",
    "swarm-coordinator":               "claude-router:swarm-coordinator",
    # short aliases
    "fast":                            "claude-router:fast-executor",
    "standard":                        "claude-router:standard-executor",
    "deep":                            "claude-router:deep-executor",
    "coordinator":                     "claude-router:swarm-coordinator",
    "swarm":                           "claude-router:swarm-coordinator",
    # model-name drift (someone wrote the cost tag instead of the executor)
    "haiku":                           "claude-router:fast-executor",
    "sonnet":                          "claude-router:standard-executor",
    "opus":                            "claude-router:deep-executor",
    # prefix drift
    "claude-router:fast":              "claude-router:fast-executor",
    "claude-router:standard":          "claude-router:standard-executor",
    "claude-router:deep":              "claude-router:deep-executor",
    # punctuation / separator drift
    "claude-router/fast-executor":     "claude-router:fast-executor",
    "claude-router/standard-executor": "claude-router:standard-executor",
    "claude-router/deep-executor":     "claude-router:deep-executor",
    "claude-router/swarm-coordinator": "claude-router:swarm-coordinator",
    "claude_router:fast-executor":     "claude-router:fast-executor",
    "claude_router:standard-executor": "claude-router:standard-executor",
    "claude_router:deep-executor":     "claude-router:deep-executor",
    "claude_router:swarm-coordinator": "claude-router:swarm-coordinator",
    # invented types that sometimes appear when a coordinator "translates"
    # the cost tag back into an executor name
    "claude-router:haiku-executor":    "claude-router:fast-executor",
    "claude-router:sonnet-executor":   "claude-router:standard-executor",
    "claude-router:opus-executor":     "claude-router:deep-executor",
}


def normalize_subagent_type(raw):
    """Attempt to map a possibly-malformed subagent_type onto a canonical value.

    Returns (canonical_value, was_normalized) or (None, False) if it cannot be
    mapped. Canonical values already in VALID_SUBAGENTS pass through unchanged.
    """
    if not isinstance(raw, str):
        return None, False

    # Already canonical
    if raw in VALID_SUBAGENTS:
        return raw, False

    # Try a lenient lookup: lower, strip whitespace, collapse internal spaces
    key = raw.strip().lower()
    key = re.sub(r"\s+", "", key)

    if key in VALID_SUBAGENTS:
        return key, raw != key

    if key in DRIFT_NORMALIZATION:
        return DRIFT_NORMALIZATION[key], True

    return None, False


def build_block_message(raw, tool_input):
    """Build a corrective message for an invalid subagent_type."""
    valid_list = "\n".join(f"  - {k}" for k in VALID_SUBAGENTS.keys())
    return (
        f"[Claude Router] Task call BLOCKED: invalid subagent_type {raw!r}.\n"
        f"\n"
        f"Valid values are exactly:\n"
        f"{valid_list}\n"
        f"\n"
        f"Copy one of these literal strings, character-for-character. Do not\n"
        f"abbreviate, do not drop the 'claude-router:' prefix, do not substitute\n"
        f"a model name (haiku/sonnet/opus) for the executor name. Re-issue the\n"
        f"Task call with a corrected subagent_type.\n"
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name != "Task":
        sys.exit(0)

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    raw = tool_input.get("subagent_type")
    if raw is None:
        # Nothing to validate - let the call through
        sys.exit(0)

    canonical, was_normalized = normalize_subagent_type(raw)

    if canonical is None:
        # Block with corrective guidance
        block_msg = build_block_message(raw, tool_input)
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": block_msg,
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    # We have a canonical subagent_type. Force the correct model to match,
    # overriding any drifted model= value the caller may have provided.
    required_model = VALID_SUBAGENTS[canonical]

    updated_input = dict(tool_input)
    modified = False

    if updated_input.get("subagent_type") != canonical:
        updated_input["subagent_type"] = canonical
        modified = True

    # Only set model if the caller passed one and it disagrees, OR if the
    # caller passed a subagent_type that drifted (in which case we pin the
    # model to match the canonical executor as a belt-and-suspenders).
    if "model" in updated_input and updated_input.get("model") != required_model:
        updated_input["model"] = required_model
        modified = True
    elif was_normalized and "model" in updated_input:
        updated_input["model"] = required_model
        modified = True

    if not modified:
        sys.exit(0)

    # Emit an allow decision with the rewritten tool_input
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": (
                f"[Claude Router] Normalized subagent_type "
                f"{raw!r} -> {canonical!r} and pinned model to {required_model}."
            ),
            "updatedInput": updated_input,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
