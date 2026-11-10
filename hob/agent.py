import logging
from dataclasses import dataclass
from importlib.resources import files

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ProcessError,
    ResultError,
    ResultMessage,
    query,
)

log = logging.getLogger(__name__)

PROMPT = files("hob").joinpath("prompt.md").read_text()

# Built-in tools the agent may use, all on the repository clone. Everything
# else Claude Code ships (Bash, Edit, Write, web access, subagents) is left out
# of `tools`, so it is not merely denied but absent.
BUILTIN_TOOLS = ["Read", "Grep", "Glob"]


@dataclass(frozen=True)
class Outcome:
    text: str
    session_id: str | None
    cost_usd: float
    turns: int
    ok: bool


def options(
    *,
    repo_dir: str,
    tool_server,
    tool_names: list[str],
    model: str,
    max_turns: int,
    max_budget_usd: float,
    resume: str | None = None,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        tools=BUILTIN_TOOLS,
        allowed_tools=[*BUILTIN_TOOLS, *tool_names],
        # Anything not allowed above is refused rather than prompted for:
        # there is nobody at a terminal to answer.
        permission_mode="dontAsk",
        mcp_servers={"hob": tool_server},
        strict_mcp_config=True,
        system_prompt={"type": "preset", "preset": "claude_code", "append": PROMPT},
        # Loads the repository's CLAUDE.md files, which describe the homelab.
        setting_sources=["project"],
        cwd=repo_dir,
        model=model,
        resume=resume,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
    )


async def run(prompt: str, opts: ClaudeAgentOptions) -> Outcome:
    try:
        async for message in query(prompt=prompt, options=opts):
            if isinstance(message, ResultMessage):
                return Outcome(
                    text=message.result or "(no answer)",
                    session_id=message.session_id,
                    cost_usd=message.total_cost_usd or 0.0,
                    turns=message.num_turns,
                    ok=not message.is_error,
                )
    except ResultError as e:
        log.warning("agent run ended with %s", e.subtype)
        limits = {
            "error_max_turns": "I hit my turn limit before finishing.",
            "error_max_budget_usd": "I hit my spending limit before finishing.",
        }
        reason = limits.get(e.subtype, f"The run failed ({e.subtype}).")
        partial = f"\n\n{e.result}" if e.result else ""
        # The CLI reports no cost on a failed run; a budget stop spent the
        # whole budget, so count that.
        cost = opts.max_budget_usd if e.subtype == "error_max_budget_usd" else 0.0
        return Outcome(reason + partial, e.session_id, cost or 0.0, 0, False)
    except ProcessError as e:
        log.exception("agent process failed")
        return Outcome(f"The agent process failed (exit {e.exit_code}).", None, 0.0, 0, False)
    return Outcome("The agent ended without a result.", None, 0.0, 0, False)
