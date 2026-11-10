import asyncio
import logging
import os
from datetime import UTC, datetime

import discord

from hob import agent, router
from hob.config import Config
from hob.store import Store
from hob.tools import Tools

log = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()


def today() -> str:
    return datetime.now(UTC).date().isoformat()


class Hob(discord.Client):
    def __init__(self, config: Config, store: Store, tools: Tools):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.store = store
        self.tools = tools
        self.pending: dict[int, asyncio.Task] = {}
        # The event loop keeps only weak references to tasks.
        self.running: set[asyncio.Task] = set()
        # One investigation at a time: they share the repository clone, and
        # parallel runs would race the daily budget.
        self.busy = asyncio.Lock()

    async def on_ready(self) -> None:
        log.info("connected as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            return
        event = router.Message(
            thread_id=channel.id,
            message_id=message.id,
            parent_id=channel.parent_id,
            author_id=message.author.id,
            from_webhook=message.webhook_id is not None,
            from_self=message.author == self.user,
            mentions_self=self.user in message.mentions,
            content=message.content,
        )
        action = router.route(
            event,
            forum_id=self.config.forum_channel_id,
            allowed_user_ids=self.config.allowed_user_ids,
            has_session=self.store.session(channel.id) is not None,
        )
        log.debug("thread %s: %s", channel.id, action)

        match action:
            case router.NewAlert():
                self.pending[channel.id] = self._spawn(
                    self._after_debounce(channel, message.content)
                )
            case router.Resolved():
                task = self.pending.pop(channel.id, None)
                if task and not task.done():
                    task.cancel()
                    log.info("thread %s resolved before investigation; skipped", channel.id)
            case router.Investigate(escalate=escalate):
                self._cancel_pending(channel.id)
                starter = await self._starter(channel)
                self._spawn(self._investigate(channel, starter, escalate, message.content))
            case router.FollowUp(text=text, escalate=escalate):
                self._spawn(self._follow_up(channel, text, escalate))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.running.add(task)
        task.add_done_callback(self.running.discard)
        return task

    def _cancel_pending(self, thread_id: int) -> None:
        task = self.pending.pop(thread_id, None)
        if task and not task.done():
            task.cancel()

    async def _starter(self, thread: discord.Thread) -> str:
        try:
            return (await thread.fetch_message(thread.id)).content
        except discord.HTTPException:
            return ""

    async def _after_debounce(self, thread: discord.Thread, starter: str) -> None:
        # Most warnings clear by themselves within a few minutes; the IRM
        # status webhook posts the resolve, which cancels this task.
        await asyncio.sleep(self.config.debounce)
        self.pending.pop(thread.id, None)
        await self._investigate(thread, starter, escalate=False, request=None)

    async def _investigate(
        self, thread: discord.Thread, starter: str, escalate: bool, request: str | None
    ) -> None:
        prompt = (
            f"Investigate this alert.\n\nForum post title: {thread.name}\n\n"
            f"Alert as posted by Grafana IRM:\n{starter}"
        )
        if request:
            prompt += f"\n\nThe human who asked you to investigate wrote:\n{request}"
        await self._run(thread, prompt, escalate, resume=None)

    async def _follow_up(self, thread: discord.Thread, text: str, escalate: bool) -> None:
        await self._run(thread, text, escalate, resume=self.store.session(thread.id))

    async def _run(
        self, thread: discord.Thread, prompt: str, escalate: bool, resume: str | None
    ) -> None:
        spent = self.store.spent(today())
        if spent >= self.config.daily_budget_usd:
            await thread.send(
                f"Skipping: today's budget of ${self.config.daily_budget_usd:.2f} is spent "
                f"(${spent:.2f}). Mention me tomorrow to pick this up.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        async with self.busy:
            try:
                await sync_repo(self.config.repo_url, self.config.repo_dir)
            except RuntimeError as e:
                log.error("repo sync failed: %s", e)
            model = self.config.escalation_model if escalate else self.config.model
            opts = agent.options(
                repo_dir=self.config.repo_dir,
                tool_server=self.tools.server(),
                tool_names=Tools.names(),
                model=model,
                max_turns=self.config.max_turns,
                max_budget_usd=self.config.max_budget_usd,
                resume=resume,
            )
            async with thread.typing():
                outcome = await agent.run(prompt, opts)

        if outcome.session_id:
            self.store.save_session(thread.id, outcome.session_id)
        self.store.add_spend(today(), outcome.cost_usd)
        footer = f"\n-# {model} · {outcome.turns} turns · ${outcome.cost_usd:.2f}"
        for chunk in router.chunks(outcome.text + footer):
            await thread.send(chunk, allowed_mentions=NO_MENTIONS)


async def sync_repo(url: str, path: str) -> None:
    """Clone the repository, or fast-forward it to origin's default branch, so
    the agent reads what is deployed."""
    if os.path.isdir(os.path.join(path, ".git")):
        steps = [
            ["git", "-C", path, "fetch", "--quiet", "origin"],
            ["git", "-C", path, "reset", "--quiet", "--hard", "origin/HEAD"],
        ]
    else:
        steps = [["git", "clone", "--quiet", "--depth", "50", url, path]]
    for step in steps:
        process = await asyncio.create_subprocess_exec(
            *step, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"{' '.join(step)} failed: {out.decode(errors='replace')}")
