#!/usr/bin/env python3
"""
Claude Router - UserPromptSubmit Hook
Classifies prompts using hybrid approach:
1. Rule-based patterns (instant, free)
2. LLM fallback for low-confidence cases:
   - SDK (if ANTHROPIC_API_KEY set) - fastest
   - CLI (claude -p) - uses existing subscription

Part of claude-router: https://github.com/Desperationis/claude-router
"""
import json
import sys
import os
import re
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

# Reentrancy guard - prevents infinite recursion when using CLI classification
# The hook sets this env var before calling `claude -p`, so the child process
# sees it and exits immediately without re-classifying
REENTRANCY_ENV_VAR = "CLAUDE_ROUTER_CLASSIFYING"
if os.environ.get(REENTRANCY_ENV_VAR) == "1":
    sys.exit(0)
# Cross-platform file locking
import platform
if platform.system() == "Windows":
    import msvcrt
    def lock_file(f, exclusive=False):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK if exclusive else msvcrt.LK_LOCK, 1)
    def unlock_file(f):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl
    def lock_file(f, exclusive=False):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    def unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

# Confidence threshold for LLM fallback
# Set high (0.95) to use LLM classification for nearly all queries
CONFIDENCE_THRESHOLD = 0.95

# Stats file location
STATS_FILE = Path.home() / ".claude" / "router-stats.json"

# Cost estimates per 1M tokens (input/output)
COST_PER_1M = {
    "fast": {"input": 1.0, "output": 5.0},        # Haiku 4.5
    "standard": {"input": 3.0, "output": 15.0},   # Sonnet 4.5
    "deep": {"input": 5.0, "output": 25.0},       # Opus 4.7
}

# Average tokens per query (rough estimate)
AVG_INPUT_TOKENS = 1000
AVG_OUTPUT_TOKENS = 2000

# Exception patterns - queries flagged as router-meta for stats attribution
# (still routed by normal classification, just tracked separately in stats)
# Pre-compiled for performance
EXCEPTION_PATTERNS = [
    re.compile(r'\brouter\b.*\b(stats?|config|setting|work)'),
    re.compile(r'\brouting\b'),
    re.compile(r'claude.?router'),
    re.compile(r'\bexception\b.*\b(track|detect)'),
    re.compile(r'\bclassif(y|ication)\b.*\b(prompt|query)'),
]

# Session state file for multi-turn context awareness
SESSION_STATE_FILE = Path.home() / ".claude" / "router-session.json"

# Number of recent messages to include for LLM context
CONTEXT_MESSAGE_LIMIT = 20

# Follow-up query patterns (pre-compiled)
FOLLOW_UP_PATTERNS = [
    re.compile(r"^(and |also |now |next |then |but )"),
    re.compile(r"^(what about|how about|can you also|could you also)"),
    re.compile(r"^(yes|no|ok|okay|sure|right|great|perfect|thanks)[,.]?\s"),
    re.compile(r"^(do that|go ahead|proceed|continue|keep going)"),
    re.compile(r"^(actually|wait|instead|rather)"),
]

# Slash command pattern - detects /word at word boundaries (not file paths)
# Must be preceded by whitespace or start of string
SLASH_COMMAND_PATTERN = re.compile(r'(?:^|\s)/[a-zA-Z][a-zA-Z0-9_-]*')


def get_session_state() -> dict:
    """Get the current session state for multi-turn context awareness."""
    try:
        if SESSION_STATE_FILE.exists():
            with open(SESSION_STATE_FILE, 'r') as f:
                state = json.load(f)
                # Check if session is stale (older than 30 minutes)
                last_query = state.get("last_query_time", 0)
                if datetime.now().timestamp() - last_query > 1800:  # 30 min
                    return {"last_route": None, "conversation_depth": 0}
                return state
        return {"last_route": None, "conversation_depth": 0}
    except Exception:
        return {"last_route": None, "conversation_depth": 0}


def update_session_state(route: str, metadata: dict = None):
    """Update session state after a routing decision."""
    try:
        SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = get_session_state()
        state["last_route"] = route
        state["last_query_time"] = datetime.now().timestamp()
        state["conversation_depth"] = state.get("conversation_depth", 0) + 1
        state["last_metadata"] = metadata or {}
        with open(SESSION_STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass  # Don't fail on state errors


def is_follow_up_query(prompt: str) -> bool:
    """Check if the query appears to be a follow-up to a previous query."""
    prompt_lower = prompt.lower().strip()
    for pattern in FOLLOW_UP_PATTERNS:
        if pattern.match(prompt_lower):
            return True
    return False


def read_transcript_context(transcript_path: str, current_prompt: str, limit: int = CONTEXT_MESSAGE_LIMIT) -> list:
    """Read recent messages from the transcript JSONL file.

    Returns a list of {"role": "user"|"assistant", "content": str} dicts.
    Excludes the current prompt if it appears at the end (not yet appended).
    """
    if not transcript_path:
        return []

    try:
        path = Path(transcript_path)
        if not path.exists():
            return []

        messages = []
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Claude Code transcript format: entry has 'message' field with role/content
                    msg = entry.get("message", entry)
                    role = msg.get("role")
                    content = msg.get("content", "")

                    # Handle content that may be a list (tool use, etc.)
                    if isinstance(content, list):
                        # Extract text content only
                        text_parts = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, str):
                                text_parts.append(part)
                        content = " ".join(text_parts)

                    if role in ("user", "assistant") and content:
                        # Truncate long messages for context
                        if len(content) > 500:
                            content = content[:500] + "..."
                        messages.append({"role": role, "content": content})
                except json.JSONDecodeError:
                    continue

        # Take only the last N messages
        messages = messages[-limit:]

        # Dedupe: if last message is the current prompt, remove it
        if messages and messages[-1]["role"] == "user":
            last_content = messages[-1]["content"].rstrip("...")
            if current_prompt.startswith(last_content) or last_content.startswith(current_prompt[:100]):
                messages = messages[:-1]

        return messages

    except Exception as e:
        print(f"[claude-router] Failed to read transcript: {e}", file=sys.stderr)
        return []


def format_context_for_llm(messages: list, current_prompt: str) -> str:
    """Format conversation context for LLM classification prompt.

    Optimized for classification accuracy:
    - Prioritizes user messages (they contain intent)
    - Skips assistant turns unless current query is a follow-up
    - Uses higher per-message budget (400 chars) for better signal
    - Limits to last 3 user messages to focus attention
    """
    if not messages:
        return f'Query: "{current_prompt}"'

    # Detect if current prompt looks like a follow-up
    prompt_lower = current_prompt.lower().strip()
    is_likely_followup = any(p.match(prompt_lower) for p in FOLLOW_UP_PATTERNS)

    # Extract only user messages (higher signal for classification)
    user_messages = [m for m in messages if m["role"] == "user"][-3:]  # Last 3 user msgs

    context_lines = []
    if user_messages:
        context_lines.append("Recent user queries:")
        for msg in user_messages:
            content = msg["content"]
            # Higher budget (400 chars) for better context
            if len(content) > 400:
                content = content[:400] + "..."
            context_lines.append(f"- {content}")

    # If follow-up, include last assistant turn to carry context
    if is_likely_followup and messages:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                content = msg["content"][:300] + "..." if len(msg["content"]) > 300 else msg["content"]
                context_lines.append(f"\nLast assistant response: {content}")
                break

    context_lines.append(f"\nCurrent query to classify: \"{current_prompt}\"")
    return "\n".join(context_lines)


def apply_context_boost(result: dict, session_state: dict, is_follow_up: bool) -> dict:
    """Apply confidence boost based on conversation context.

    If this is a follow-up to a deep/complex query, boost confidence toward same route.
    """
    if not is_follow_up:
        return result

    last_route = session_state.get("last_route")
    if not last_route:
        return result

    result["metadata"] = result.get("metadata", {})
    result["metadata"]["follow_up"] = True

    # If last route was deep/standard, boost current toward same
    # (follow-ups to complex queries are often also complex)
    if last_route in ("deep", "standard") and result["route"] == "fast":
        if result["confidence"] < 0.8:
            result["confidence"] = min(0.75, result["confidence"] + 0.15)
            result["metadata"]["context_boost"] = f"follow_up_to_{last_route}"
            # Don't change route, just boost confidence to potentially trigger LLM

    return result


def is_exception_query(prompt: str) -> tuple[bool, str | None]:
    """Check if query matches router-meta patterns for stats attribution.

    Returns (True, 'router_meta') for queries about the router itself.
    These are still routed normally, just tracked separately in stats.
    """
    prompt_lower = prompt.lower()
    for pattern in EXCEPTION_PATTERNS:
        if pattern.search(prompt_lower):  # Pre-compiled patterns use .search()
            return True, "router_meta"
    return False, None

# Classification patterns - Pre-compiled for performance
PATTERNS = {
    "fast": [
        # Simple questions
        re.compile(r"^what (is|are|does) "),
        re.compile(r"^how (do|does|to) "),
        re.compile(r"^(show|list|get) .{0,30}$"),
        # Formatting
        re.compile(r"\b(format|lint|prettify|beautify)\b"),
        # Git simple ops
        re.compile(r"\bgit (status|log|diff|add|commit|push|pull)\b"),
        # JSON/YAML
        re.compile(r"\b(json|yaml|yml)\b.{0,20}$"),
        # Regex
        re.compile(r"\bregex\b"),
        # Syntax questions
        re.compile(r"\bsyntax (for|of)\b"),
        re.compile(r"^(what|how).{0,50}\?$"),
    ],
    "deep": [
        # Architecture
        re.compile(r"\b(architect|architecture|design pattern|system design)\b"),
        re.compile(r"\bscalable?\b"),
        # Security
        re.compile(r"\b(security|vulnerab|audit|penetration|exploit)\b"),
        # Multi-file
        re.compile(r"\b(across|multiple|all) (files?|components?|modules?)\b"),
        re.compile(r"\brefactor.{0,20}(codebase|project|entire)\b"),
        # Trade-offs
        re.compile(r"\b(trade-?off|compare|pros? (and|&) cons?)\b"),
        re.compile(r"\b(analyze|evaluate|assess).{0,30}(option|approach|strateg)\b"),
        # Complex
        re.compile(r"\b(complex|intricate|sophisticated)\b"),
        re.compile(r"\boptimiz(e|ation).{0,20}(performance|speed|memory)\b"),
        # Planning
        re.compile(r"\b(multi-?phase|extraction|standalone repo|migration)\b"),
    ],
    "tool_intensive": [
        # Codebase exploration
        re.compile(r"\b(find|search|locate) (all|every|each)"),
        re.compile(r"\bacross (the )?(codebase|project|repo)"),
        re.compile(r"\b(all|every) (file|instance|usage|reference)"),
        re.compile(r"\bwhere is .+ (used|called|defined)"),
        re.compile(r"\b(scan|explore|traverse) (the )?(codebase|project)"),
        # Multi-file modifications
        re.compile(r"\b(update|change|modify|rename|replace) .{0,20}(all|every|multiple) files?"),
        re.compile(r"\bglobal (search|replace|rename)"),
        re.compile(r"\brefactor.{0,30}(across|throughout|entire)"),
        # Build/test execution
        re.compile(r"\brun (all |the )?(tests?|specs?|suite)"),
        re.compile(r"\bbuild (the )?(project|app)"),
        re.compile(r"\bnpm (install|build|run)|yarn (install|build)|pip install"),
        # Dependency analysis
        re.compile(r"\b(dependency|import) (tree|graph|analysis)"),
        re.compile(r"\bwhat (depends on|imports|uses)"),
    ],
    "orchestration": [
        # Multi-step workflows
        re.compile(r"\b(step by step|sequentially|in order)\b"),
        re.compile(r"\bfor each (file|component|module)\b"),
        re.compile(r"\bacross the (entire|whole) (codebase|project)"),
        # Explicit multi-task
        re.compile(r"\band (also|then)\b.{0,50}\band (also|then)\b"),
        re.compile(r"\b(multiple|several|many) (tasks?|steps?|operations?)\b"),
    ],
    "debugging": [
        # Explicit debugging/error language (with and without apostrophes for informal typing)
        re.compile(r"\b(doesn'?t|does not|isn'?t|is not|won'?t|will not|can'?t|cannot) (work|run|compile|build|start|load)\b"),
        re.compile(r"\bnot working\b"),
        re.compile(r"\b(this|it|that).{0,20}(doesn'?t|isn'?t|won'?t) work\b"),
        re.compile(r"\bisn'?t working\b"),
        re.compile(r"\b(broken|breaking|broke)\b"),
        re.compile(r"\b(bug|bugs|buggy)\b"),
        re.compile(r"\b(error|errors|erroring)\b"),
        re.compile(r"\b(fail|fails|failing|failed)\b"),
        re.compile(r"\b(crash|crashes|crashing|crashed)\b"),
        re.compile(r"\b(wrong|incorrect|unexpected)\s+(output|result|behavior|value)\b"),
        re.compile(r"\bhelp.{0,15}(debug|figure out|understand why|find out why)\b"),
        re.compile(r"\b(fix|debug|troubleshoot)\b"),
        re.compile(r"\bwhy (is|does|doesn't|isn't|won't)\b"),
        re.compile(r"\b(don'?t|do not) know (why|what|how)\b"),
        re.compile(r"\bno idea (why|what|how)\b"),
        re.compile(r"\bcan'?t (figure out|understand|tell)\b"),
        re.compile(r"\bsomething.{0,20}(wrong|off|broken|weird)\b"),
        re.compile(r"\bit (says|shows|returns|gives).{0,30}(error|exception|null|undefined|NaN)\b"),
        re.compile(r"\b(throws?|throwing|raised?|raising) (an? )?(error|exception)\b"),
        re.compile(r"\bgetting (an? )?(error|exception|warning)\b"),
    ],
}


def get_api_key():
    """Get API key from environment or common locations."""
    # Try environment first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    # Try common .env locations
    search_paths = [
        Path.cwd() / ".env",                           # Current directory
        Path.cwd() / "server" / ".env",                # Server subdirectory
        Path.home() / ".anthropic" / "api_key",        # Anthropic config
        Path.home() / ".config" / "anthropic" / "key", # XDG config
    ]

    for env_path in search_paths:
        try:
            with open(env_path, "r") as f:
                content = f.read()
                # Handle both KEY=value and plain value formats
                for line in content.split("\n"):
                    if line.startswith("ANTHROPIC_API_KEY="):
                        return line.strip().split("=", 1)[1].strip('"\'')
                # If file is just the key (no assignment)
                if content.strip().startswith("sk-ant-"):
                    return content.strip()
        except (FileNotFoundError, PermissionError):
            continue

    return None


def calculate_cost(route: str, input_tokens: int = AVG_INPUT_TOKENS, output_tokens: int = AVG_OUTPUT_TOKENS) -> float:
    """Calculate estimated cost for a route."""
    costs = COST_PER_1M[route]
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    return input_cost + output_cost


def log_routing_decision(route: str, confidence: float, method: str, signals: list, metadata: dict = None):
    """Log routing decision to stats file with optional metadata tracking."""
    try:
        # Ensure directory exists
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Load existing stats or create new (v1.2 schema with exception tracking)
        stats = {
            "version": "1.3",
            "total_queries": 0,
            "routes": {"fast": 0, "standard": 0, "deep": 0, "complex": 0},
            "exceptions": {"router_meta": 0, "slash_commands": 0, "explicit_route": 0, "explicit_retry": 0},
            "tool_intensive_queries": 0,
            "complex_queries": 0,
            "estimated_savings": 0.0,
            "delegation_savings": 0.0,
            "total_actual_cost": 0.0,
            "total_opus_cost": 0.0,
            "sessions": [],
            "last_updated": None
        }

        if STATS_FILE.exists():
            try:
                with open(STATS_FILE, "r") as f:
                    lock_file(f, exclusive=False)
                    stats = json.load(f)
                    unlock_file(f)
            except (json.JSONDecodeError, IOError):
                pass

        # Ensure v1.3 schema fields exist (migration from v1.0/v1.1/v1.2)
        stats.setdefault("version", "1.3")
        stats.setdefault("routes", {}).setdefault("complex", 0)
        # Properly migrate exceptions dict - merge sub-keys individually
        exceptions = stats.setdefault("exceptions", {})
        for key in ["router_meta", "slash_commands", "explicit_route", "explicit_retry"]:
            exceptions.setdefault(key, 0)
        stats.setdefault("tool_intensive_queries", 0)
        stats.setdefault("complex_queries", 0)
        stats.setdefault("delegation_savings", 0.0)
        stats.setdefault("total_actual_cost", 0.0)
        stats.setdefault("total_opus_cost", 0.0)

        metadata = metadata or {}
        exception_type = metadata.get("exception_type")

        # Exceptions (slash commands, explicit routes, etc.) are tracked separately
        # and don't count toward route distribution or savings
        if exception_type:
            stats["exceptions"][exception_type] = stats["exceptions"].get(exception_type, 0) + 1
            # Exceptions still count toward total_queries for overall usage tracking
            stats["total_queries"] += 1
            # But they don't get route counts or savings - exit early after updating timestamp
            stats["last_updated"] = datetime.now().isoformat()
            with open(STATS_FILE, "w") as f:
                lock_file(f, exclusive=True)
                json.dump(stats, f, indent=2)
                unlock_file(f)
            return

        # Regular classification - update all stats
        stats["total_queries"] += 1

        # Track complex vs regular routes
        # Orchestration counts as complex regardless of underlying route
        if metadata.get("orchestration"):
            stats["routes"]["complex"] += 1
            stats["complex_queries"] += 1
        else:
            stats["routes"][route] += 1

        # Track tool-intensive queries
        if metadata.get("tool_intensive"):
            stats["tool_intensive_queries"] += 1

        # Calculate savings (compared to always using Opus)
        actual_cost = calculate_cost(route)
        opus_cost = calculate_cost("deep")
        savings = opus_cost - actual_cost
        stats["estimated_savings"] += savings
        stats["total_actual_cost"] += actual_cost
        stats["total_opus_cost"] += opus_cost

        # Calculate delegation savings for complex queries
        # Assumes 60% delegation (70% Haiku, 30% Sonnet) saves ~40% vs pure Opus
        if metadata.get("orchestration"):
            delegation_saving = opus_cost * 0.4  # ~40% savings through delegation
            stats["delegation_savings"] += delegation_saving

        # Get or create today's session
        today = datetime.now().strftime("%Y-%m-%d")
        session = None
        for s in stats.get("sessions", []):
            if s["date"] == today:
                session = s
                break

        if not session:
            session = {
                "date": today,
                "queries": 0,
                "routes": {"fast": 0, "standard": 0, "deep": 0, "complex": 0},
                "savings": 0.0
            }
            stats.setdefault("sessions", []).append(session)

        session["queries"] += 1
        # Session routes should match global route tracking
        if metadata.get("orchestration"):
            session["routes"].setdefault("complex", 0)
            session["routes"]["complex"] += 1
        else:
            session["routes"][route] += 1
        session["savings"] += savings

        # Keep only last 30 days of sessions
        stats["sessions"] = sorted(stats["sessions"], key=lambda x: x["date"], reverse=True)[:30]

        stats["last_updated"] = datetime.now().isoformat()

        # Write stats atomically
        with open(STATS_FILE, "w") as f:
            lock_file(f, exclusive=True)
            json.dump(stats, f, indent=2)
            unlock_file(f)

    except Exception as e:
        # Don't fail the hook if stats logging fails, but log to stderr for debugging
        import sys as _sys
        print(f"[claude-router] Stats update failed: {e}", file=_sys.stderr)


def classify_by_rules(prompt: str) -> dict:
    """
    Classify prompt using pre-compiled regex patterns.
    Returns route, confidence, signals, and optional metadata.

    Priority order:
    1. deep patterns (architecture, security, complex analysis)
    2. tool_intensive patterns (route to standard, or deep if combined)
    3. orchestration patterns (route to deep with orchestration flag)
    4. fast patterns (simple queries)

    Optimized with early exit when sufficient signals are found.
    """
    prompt_lower = prompt.lower()
    deep_signals = []
    tool_signals = []
    orch_signals = []
    debug_signals = []

    # Check for deep patterns first (highest priority)
    # Pre-compiled patterns use .search() method directly
    for pattern in PATTERNS["deep"]:
        match = pattern.search(prompt_lower)
        if match:
            deep_signals.append(match.group(0))
            # Early exit: if we have 3+ deep signals, no need to check more
            if len(deep_signals) >= 3:
                break

    # Check for debugging/complaint patterns (user saying something doesn't work)
    for pattern in PATTERNS.get("debugging", []):
        match = pattern.search(prompt_lower)
        if match:
            debug_signals.append(match.group(0))
            # Early exit: if we have 2+ debug signals, we have enough
            if len(debug_signals) >= 2:
                break

    # Check for tool-intensive patterns
    for pattern in PATTERNS.get("tool_intensive", []):
        match = pattern.search(prompt_lower)
        if match:
            tool_signals.append(match.group(0))
            # Early exit: if we have deep + tool signals, we have enough
            if deep_signals and len(tool_signals) >= 2:
                break

    # Check for orchestration patterns
    for pattern in PATTERNS.get("orchestration", []):
        match = pattern.search(prompt_lower)
        if match:
            orch_signals.append(match.group(0))
            # Early exit: if we have deep + orchestration, we have enough
            if deep_signals:
                break

    # Decision matrix: deep + tool_intensive + orchestration
    if deep_signals and (tool_signals or orch_signals):
        # Complex task needing orchestration - route to deep with orchestration flag
        combined = deep_signals + tool_signals + orch_signals
        return {
            "route": "deep",
            "confidence": 0.95,
            "signals": combined[:4],
            "method": "rules",
            "metadata": {"orchestration": True, "tool_intensive": bool(tool_signals)}
        }

    if len(deep_signals) >= 2:
        return {"route": "deep", "confidence": 0.9, "signals": deep_signals[:3], "method": "rules"}

    if deep_signals:  # One deep signal
        return {"route": "deep", "confidence": 0.7, "signals": deep_signals, "method": "rules"}

    # Debugging/complaint patterns - user says something doesn't work
    # Route to standard (known-ish location) or deep (vague/unknown root cause)
    if debug_signals:
        # Vague complaints with no specific location → deep (unknown root cause)
        vague_patterns = [
            re.compile(r"\bsomething\b"),
            re.compile(r"\bsomewhere\b"),
            re.compile(r"\b(don'?t|do not) know (why|what|where|how)\b"),
            re.compile(r"\bno idea (why|what|how)\b"),
            re.compile(r"\b(can'?t|cannot) (find|figure|understand)\b"),
            re.compile(r"\bwhy (is|does|doesn'?t|isn'?t)\b"),
        ]
        is_vague = any(p.search(prompt_lower) for p in vague_patterns)

        if is_vague or len(debug_signals) >= 3:
            return {
                "route": "deep",
                "confidence": 0.85,
                "signals": debug_signals[:3],
                "method": "rules",
                "metadata": {"debugging": True, "vague_complaint": True}
            }
        if len(debug_signals) >= 2:
            return {
                "route": "standard",
                "confidence": 0.85,
                "signals": debug_signals[:3],
                "method": "rules",
                "metadata": {"debugging": True}
            }
        # Single debug signal - route to standard with moderate confidence
        return {
            "route": "standard",
            "confidence": 0.75,
            "signals": debug_signals,
            "method": "rules",
            "metadata": {"debugging": True}
        }

    # Tool-intensive but not architecturally complex - route to standard
    if tool_signals:
        if len(tool_signals) >= 2:
            return {
                "route": "standard",
                "confidence": 0.85,
                "signals": tool_signals[:3],
                "method": "rules",
                "metadata": {"tool_intensive": True}
            }
        return {
            "route": "standard",
            "confidence": 0.7,
            "signals": tool_signals,
            "method": "rules",
            "metadata": {"tool_intensive": True}
        }

    # Orchestration alone (multi-step workflow) - route to standard
    if orch_signals:
        return {
            "route": "standard",
            "confidence": 0.75,
            "signals": orch_signals[:3],
            "method": "rules",
            "metadata": {"orchestration": True}
        }

    # Check for fast patterns
    fast_signals = []
    for pattern in PATTERNS["fast"]:
        match = pattern.search(prompt_lower)
        if match:
            fast_signals.append(match.group(0))
            if len(fast_signals) >= 2:
                return {"route": "fast", "confidence": 0.9, "signals": fast_signals[:3], "method": "rules"}

    if fast_signals:  # One fast signal
        return {"route": "fast", "confidence": 0.7, "signals": fast_signals, "method": "rules"}

    # Default to fast with low confidence - cheaper when uncertain
    return {"route": "fast", "confidence": 0.5, "signals": ["no strong patterns"], "method": "rules"}


def classify_by_llm(prompt: str, api_key: str, context_messages: list = None) -> dict:
    """
    Classify prompt using Haiku LLM.
    Used as fallback for low-confidence rule-based results.

    Args:
        prompt: The current user prompt to classify
        api_key: Anthropic API key
        context_messages: Optional list of recent conversation messages for context
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    client = Anthropic(api_key=api_key)

    # Format query with conversation context if available
    query_section = format_context_for_llm(context_messages or [], prompt)

    classification_prompt = f"""You are a fast, precise router. Given a coding query and context, pick exactly one route. Return ONLY valid JSON.

{query_section}

ROUTES (pick the highest that applies):

deep (Opus - $15/MTok input):
  - Designing NEW systems: architecture, data models, API contracts
  - Security audits, threat analysis, vulnerability assessment
  - Unknown-root-cause debugging (could be anywhere in the system)
  - Trade-off analysis with significant downstream impact
  - Multi-phase migrations, extractions, major refactors
  NOT deep: mechanical refactors with clear plan, "touch many files" where each edit is straightforward

standard (Sonnet - $3/MTok input):
  - Implementing features with clear spec
  - Fixing bugs when location is known/narrowable
  - Refactoring code whose shape is understood
  - Writing tests, adding validation, medium code changes
  - Multi-file edits where plan is clear but each edit needs judgment
  - Code review of specific changes

fast (Haiku - $1/MTok input):
  - Questions answerable from docs/knowledge (syntax, APIs, "how do I...")
  - Pure search: grep, glob, "where is X defined/used"
  - Mechanical single-file edits with exact before/after specified
  - Read-only git: status, log, diff, blame
  - JSON/YAML/config inspection or reformatting
  - Questions about this router or its configuration

DISAMBIGUATION RULES (apply in order):
1. Follow-up ("yes", "do it", "continue") = inherit prior query's complexity
2. "Find all X" alone = fast. "Find X and replace with Y" = standard. "Find X, decide relevance, then fix" = standard/deep
3. "Refactor file F" = standard. "Refactor the codebase" = deep
4. "Fix bug in function F" = standard. "Something broken, help me find it" = deep
5. 3+ distinct steps where any step is deep = deep. Otherwise = standard
6. Tool count doesn't matter. 20 greps is still fast if no judgment on results

CONFIDENCE RUBRIC:
  0.95-1.0: Unambiguous, exactly matches one category
  0.80-0.94: Clearly one category, some signals point at neighbor
  0.60-0.79: Borderline between two - pick simpler (cost bias)
  <0.60: Never emit - return simpler route with 0.60

EXAMPLES:
  "what's the syntax for Python walrus operator" -> fast (0.98) - syntax lookup
  "rename getUserById to fetchUserById in users.ts" -> fast (0.95) - single-file mechanical rename
  "find all usages of useAuth in the codebase" -> fast (0.95) - pure grep, no judgment
  "add pagination to the /users endpoint" -> standard (0.90) - feature with clear scope
  "fix the null pointer in the login handler" -> standard (0.88) - bug fix, location known
  "refactor the auth module to use dependency injection" -> standard (0.85) - refactor with clear goal
  "something is wrong with our auth, users get random 401s" -> deep (0.85) - unknown-root-cause debugging
  "design a caching layer for our API" -> deep (0.92) - architecture, designing new system
  "review the security implications of this change" -> deep (0.88) - security audit

Return JSON only (no markdown, no prose):
{{"route": "fast|standard|deep", "confidence": 0.0-1.0, "signals": ["signal1", "signal2"], "reason": "short explanation max 15 words", "tool_intensive": true|false}}"""

    # Assistant prefill forces structured JSON output
    assistant_prefill = '{"route":"'

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,  # Reduced since prefill handles structure
            messages=[
                {"role": "user", "content": classification_prompt},
                {"role": "assistant", "content": assistant_prefill}
            ],
            stop_sequences=["\n\n", "```"]  # Stop before any trailing prose
        )

        # Reconstruct full JSON by prepending the prefill
        response_text = assistant_prefill + message.content[0].text.strip()

        # Handle potential markdown code blocks (rare with prefill, but safe)
        if "```" in response_text:
            response_text = response_text.split("```")[0].strip()

        result = json.loads(response_text)
        result["method"] = "haiku-llm"
        # Ensure a reason is always present so downstream code can rely on it
        if not result.get("reason"):
            signals = result.get("signals") or []
            result["reason"] = ", ".join(signals[:2]) if signals else "classified by Haiku"
        return result

    except Exception as e:
        # Log error but don't fail
        print(f"LLM classification error: {e}", file=sys.stderr)
        return None


def classify_by_cli(prompt: str, context_messages: list = None) -> dict:
    """
    Classify prompt using the claude CLI with Haiku.
    Used as fallback when no API key is set but claude CLI is available.
    Uses the user's existing Claude subscription (OAuth/keychain auth).

    Args:
        prompt: The current user prompt to classify
        context_messages: Optional list of recent conversation messages for context

    Returns None on any failure (timeout, CLI not found, parse error).
    """
    # Check if claude CLI is available
    if not shutil.which("claude"):
        return None

    # Build context section if available. Pass up to the last 20 messages so
    # the CLI classifier sees enough history to judge follow-up queries.
    context_section = ""
    if context_messages:
        context_lines = []
        for msg in context_messages[-20:]:  # Last 20 messages
            role = "U" if msg["role"] == "user" else "A"
            content = msg["content"][:200]  # Keep each line short for latency
            context_lines.append(f"{role}: {content}")
        if context_lines:
            context_section = "Recent context:\n" + "\n".join(context_lines) + "\n\n"

    # Ask Haiku for both the route and a short reason. Latency cost of the
    # extra reason token is negligible compared to the CLI startup itself.
    classification_prompt = f'''{context_section}Classify this query. Reply with ONE line of JSON only:
{{"route": "fast|standard|deep", "reason": "max 15 words"}}

Query: "{prompt[:200]}"

ROUTES:
fast = syntax/API questions, grep/search, single-file mechanical edits, git status/log/diff
standard = bug fixes (location known), features, refactoring, tests, multi-file edits with clear plan
deep = architecture/design, security audits, unknown-root-cause debugging, migrations

RULES:
- Follow-up ("yes", "do it") = inherit prior complexity
- "Find all X" = fast. "Find X and change to Y" = standard
- "Refactor file" = standard. "Refactor codebase" = deep
- Tool count doesn't matter. 20 greps = fast if no judgment needed

EXAMPLES:
"what's the syntax for async/await" -> fast - syntax lookup
"find all usages of useAuth" -> fast - pure search
"add pagination to /users" -> standard - feature, clear scope
"random 401 errors, help debug" -> deep - unknown root cause

Reason must explain WHY, not restate route name.

JSON:'''

    try:
        # Set reentrancy guard to prevent infinite loop
        env = os.environ.copy()
        env[REENTRANCY_ENV_VAR] = "1"

        # Call claude CLI with Haiku model
        # --print: non-interactive mode, just print response
        # --model haiku: use cheapest/fastest model
        # --output-format json: get structured output envelope
        result = subprocess.run(
            ["claude", "--print", "--model", "haiku", "--output-format", "json", classification_prompt],
            capture_output=True,
            text=True,
            timeout=8.0,  # Allow up to 8s for CLI startup + API call
            env=env,
        )

        if result.returncode != 0:
            return None

        # Parse the outer JSON envelope from --output-format json
        try:
            outer = json.loads(result.stdout)
            # The actual response is in the 'result' field
            response_text = outer.get("result", "").strip()
        except json.JSONDecodeError:
            response_text = result.stdout.strip()

        # Strip any markdown fences the model might emit around the JSON
        cleaned = response_text.replace("```json", "").replace("```", "").strip()

        # First try to parse the inner payload as JSON (route + reason)
        route = None
        reason = None
        try:
            # Find the first { ... } block in case the model added prose
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                inner = json.loads(cleaned[start:end + 1])
                raw_route = str(inner.get("route", "")).strip().lower()
                if raw_route in ("fast", "standard", "deep"):
                    route = raw_route
                reason = (inner.get("reason") or "").strip() or None
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        # Fall back to keyword sniffing if JSON parsing failed (older prompts,
        # truncated output, or models that ignored the format instruction)
        if route is None:
            lowered = cleaned.lower()[:40]
            if "deep" in lowered:
                route = "deep"
            elif "standard" in lowered:
                route = "standard"
            elif "fast" in lowered:
                route = "fast"
            else:
                return None

        if not reason:
            reason = "classified by Haiku CLI"

        return {
            "route": route,
            "confidence": 0.85,  # LLM classification is generally reliable
            "signals": ["cli-classified"],
            "reason": reason,
            "method": "haiku-cli"
        }

    except subprocess.TimeoutExpired:
        print("[claude-router] CLI classification timed out, falling back to rules", file=sys.stderr)
        return None
    except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
        print(f"[claude-router] CLI classification error: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[claude-router] CLI classification unexpected error: {e}", file=sys.stderr)
        return None


def classify_hybrid(prompt: str, transcript_path: str = None) -> dict:
    """
    Hybrid classification: rules first, then context boost, then LLM fallback.

    LLM fallback priority:
    1. SDK (if ANTHROPIC_API_KEY set) - fastest, ~500ms
    2. CLI (if claude command available) - uses subscription, ~1-2s
    3. Return rule-based result

    Args:
        prompt: The current user prompt to classify
        transcript_path: Optional path to conversation transcript for context
    """
    # Step 1: Rule-based classification (instant, free)
    result = classify_by_rules(prompt)

    # Step 2: Check for multi-turn context (follow-up queries)
    session_state = get_session_state()
    follow_up = is_follow_up_query(prompt)
    if follow_up:
        result = apply_context_boost(result, session_state, follow_up)

    # Step 3: If low confidence, try LLM classification with conversation context
    if result["confidence"] < CONFIDENCE_THRESHOLD:
        # Read conversation context from transcript
        context_messages = read_transcript_context(transcript_path, prompt)

        # Priority 1: SDK (fastest, requires API key)
        api_key = get_api_key()
        if api_key:
            llm_result = classify_by_llm(prompt, api_key, context_messages)
            if llm_result:
                return llm_result

        # Priority 2: CLI (uses existing subscription, no API key needed)
        cli_result = classify_by_cli(prompt, context_messages)
        if cli_result:
            return cli_result

    return result


def build_routing_reason(result: dict) -> str:
    """Produce a short, user-visible sentence explaining why a route was picked.

    Priority order:
    1. Explicit "reason" field returned by the LLM (SDK or CLI path).
    2. Synthesized reason from metadata flags (orchestration, tool-intensive,
       follow-up, context-boost).
    3. Fallback built from the signals list produced by the rule engine.
    4. Generic per-route fallback if nothing else is available.

    The returned string is capped at ~120 characters so it renders cleanly on
    one status line in the CLI.
    """
    route = result.get("route", "fast")

    # 1. Prefer an explicit reason from the LLM
    explicit = (result.get("reason") or "").strip()
    if explicit:
        return explicit[:120]

    metadata = result.get("metadata") or {}

    # 2. Synthesize from metadata
    metadata_reasons = []
    if metadata.get("debugging"):
        if metadata.get("vague_complaint"):
            metadata_reasons.append("debugging request with unknown root cause")
        else:
            metadata_reasons.append("debugging/troubleshooting request")
    if metadata.get("orchestration"):
        metadata_reasons.append("multi-step orchestration")
    if metadata.get("tool_intensive"):
        metadata_reasons.append("tool-intensive work")
    if metadata.get("follow_up"):
        last = metadata.get("context_boost", "").replace("follow_up_to_", "") or "previous turn"
        metadata_reasons.append(f"follow-up to {last} query")
    if metadata_reasons:
        return ", ".join(metadata_reasons)[:120]

    # 3. Build from signals
    signals = result.get("signals") or []
    meaningful = [s for s in signals if s and s != "no strong patterns" and s != "cli-classified"]
    if meaningful:
        joined = ", ".join(meaningful[:2])
        return f"matched {joined}"[:120]

    # 4. Per-route fallback
    defaults = {
        "fast": "no strong complexity signals detected",
        "standard": "moderate complexity detected",
        "deep": "high-complexity architectural signals detected",
    }
    return defaults.get(route, "router default")


def main():
    """Main hook handler."""
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    try:
        _main_inner(input_data)
    except Exception as e:
        # Log to stderr for debugging, but exit cleanly to avoid blocking user
        print(f"[claude-router] Hook crashed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)


def _main_inner(input_data: dict):
    """Inner main logic, wrapped by main() for error handling."""
    prompt = input_data.get("prompt", "")

    if not prompt or len(prompt) < 10:
        sys.exit(0)

    # Check for slash commands anywhere in the prompt (e.g., /confirm, /route)
    # If found, skip routing entirely - let the current model handle it
    if SLASH_COMMAND_PATTERN.search(prompt):
        cmd_match = SLASH_COMMAND_PATTERN.search(prompt)
        cmd_name = cmd_match.group(0) if cmd_match else "/"
        log_routing_decision("none", 1.0, "slash_command", [cmd_name], {"exception_type": "slash_commands"})
        sys.exit(0)

    # Handle slash commands (legacy path - prompts starting with /)
    stripped = prompt.strip().lower()
    if stripped.startswith("/"):
        # Special handling for /route with explicit model
        if stripped.startswith("/route "):
            route_args = prompt.strip()[7:].strip()  # Get everything after "/route "
            first_word = route_args.split()[0].lower() if route_args.split() else ""

            # Check for explicit model specification
            model_map = {
                "opus": ("deep", "deep-executor", "Opus"),
                "deep": ("deep", "deep-executor", "Opus"),
                "sonnet": ("standard", "standard-executor", "Sonnet"),
                "standard": ("standard", "standard-executor", "Sonnet"),
                "haiku": ("fast", "fast-executor", "Haiku"),
                "fast": ("fast", "fast-executor", "Haiku"),
            }

            if first_word in model_map:
                route, subagent, model = model_map[first_word]
                query = " ".join(route_args.split()[1:])  # Rest after model

                # Log explicit route command to stats
                log_routing_decision(route, 1.0, "explicit", [f"/route {first_word}"], {"exception_type": "explicit_route"})

                # Update session state for follow-up detection
                update_session_state(route, {"explicit": True})

                context = f"""[Claude Router] EXPLICIT MODEL OVERRIDE
Route: {route} | Model: {model} | Source: User specified "{first_word}"

USER EXPLICITLY REQUESTED {model.upper()}. This is NOT a suggestion - it is a COMMAND.

CRITICAL: Spawn "claude-router:{subagent}" with the query below. DO NOT reclassify. DO NOT override.

Query: {query}

Example:
Task(subagent_type="claude-router:{subagent}", prompt="{query}", description="Route to {model}")"""

                output = {
                    "systemMessage": f"→ Routing to {model} (explicit)",
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": context
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

        # Special handling for /retry with explicit model
        if stripped.startswith("/retry "):
            retry_args = prompt.strip()[7:].strip().lower()  # Get everything after "/retry "

            retry_model_map = {
                "opus": ("deep", "deep-executor", "Opus"),
                "deep": ("deep", "deep-executor", "Opus"),
                "sonnet": ("standard", "standard-executor", "Sonnet"),
                "standard": ("standard", "standard-executor", "Sonnet"),
            }

            if retry_args in retry_model_map:
                route, subagent, model = retry_model_map[retry_args]

                # Log explicit retry command to stats
                log_routing_decision(route, 1.0, "explicit", [f"/retry {retry_args}"], {"exception_type": "explicit_retry"})

                # Update session state for follow-up detection
                update_session_state(route, {"explicit": True, "retry": True})

                context = f"""[Claude Router] EXPLICIT RETRY OVERRIDE
Route: {route} | Model: {model} | Source: User specified "/retry {retry_args}"

USER EXPLICITLY REQUESTED {model.upper()} FOR RETRY. This is NOT a suggestion - it is a COMMAND.

CRITICAL: Read the last query from session state (~/.claude/router-session.json) and spawn "claude-router:{subagent}".
DO NOT auto-escalate. DO NOT choose a different model. Use {model.upper()}.

Example:
Task(subagent_type="claude-router:{subagent}", prompt="<last query from session>", description="Retry with {model}")"""

                output = {
                    "systemMessage": f"→ Retrying with {model} (explicit)",
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": context
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

        # Special handling for /swarm - DO NOT route to a subagent.
        # The swarm orchestrator needs to spawn worker agents, but subagents
        # cannot spawn further subagents (Agent tool not available to them).
        # Solution: Let the main agent handle swarm orchestration directly.
        # The skill will tell main agent to read swarm-coordinator.md and
        # follow those instructions, spawning worker agents from top-level.
        if stripped.startswith("/swarm"):
            # Log /swarm for stats tracking
            log_routing_decision(
                "deep",
                1.0,
                "explicit",
                ["/swarm"],
                {"exception_type": "slash_commands", "slash_command": "/swarm"},
            )

            # Update session state so follow-ups see this as a deep route
            update_session_state("deep", {"explicit": True, "slash_command": "/swarm"})

            # Exit WITHOUT emitting a routing directive - let the skill handle it
            # The skill will tell main agent to orchestrate directly (not as subagent)
            sys.exit(0)

        # Log slash command to stats (skills handle the actual command)
        # Extract command name for tracking - use "none" as route since no routing happens
        cmd_name = stripped.split()[0] if stripped.split() else "/"
        log_routing_decision("none", 1.0, "slash_command", [cmd_name], {"exception_type": "slash_commands"})
        sys.exit(0)

    # Check for exception queries (router meta-questions)
    is_exception, exception_type = is_exception_query(prompt)

    # Get transcript path for conversation context
    transcript_path = input_data.get("transcript_path")

    # Classify using hybrid approach with conversation context
    result = classify_hybrid(prompt, transcript_path)

    route = result["route"]
    confidence = result["confidence"]
    signals = result["signals"]
    method = result.get("method", "rules")

    # Get metadata for orchestration/tool-intensive routing
    metadata = result.get("metadata", {})

    # Track exception if detected
    if is_exception:
        metadata["exception_type"] = exception_type

    # Log routing decision to stats
    log_routing_decision(route, confidence, method, signals, metadata)

    # Update session state for multi-turn context awareness
    update_session_state(route, metadata)

    # Map route to subagent and model
    # Note: /swarm is explicit - complex tasks route to deep-executor, user invokes /swarm for parallel agents
    subagent_map = {"fast": "fast-executor", "standard": "standard-executor", "deep": "deep-executor"}
    model_map = {"fast": "Haiku", "standard": "Sonnet", "deep": "Opus"}
    subagent = subagent_map[route]
    model = model_map[route]

    signals_str = ", ".join(signals)

    # Add metadata info to context
    metadata_str = ""
    if metadata.get("tool_intensive"):
        metadata_str += " | Tool-intensive: Yes"
    if metadata.get("orchestration"):
        metadata_str += " | Orchestration: Yes"
    if metadata.get("follow_up"):
        metadata_str += " | Follow-up: Yes"
    if metadata.get("context_boost"):
        metadata_str += f" | Context: {metadata['context_boost']}"
    if metadata.get("exception_type"):
        metadata_str += f" | Exception: {metadata['exception_type']}"

    context = f"""[Claude Router] MANDATORY ROUTING DIRECTIVE
Route: {route} | Model: {model} | Confidence: {confidence:.0%} | Method: {method}{metadata_str}
Signals: {signals_str}

CRITICAL: You MUST use the Task tool NOW to spawn the "claude-router:{subagent}" subagent.
Do NOT respond to the user directly. Do NOT skip this step. Delegate immediately.

Example:
Task(subagent_type="claude-router:{subagent}", prompt="<user's query>", description="Route to {model}")"""

    # Build a user-visible reason sentence instead of a confidence percentage.
    # The internal routing directive (additionalContext) still carries the
    # numeric confidence for the Task subagent; only the surface UI changes.
    reason_text = build_routing_reason(result)

    # Output as JSON with hookSpecificOutput for context injection
    # and systemMessage for user-visible feedback
    output = {
        "systemMessage": f"→ Routing to {model}: {reason_text}",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
