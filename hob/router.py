"""Decides what a Discord message in the alerts forum means for Hob. Kept free
of discord.py so it can be tested on plain values."""

import re
from dataclasses import dataclass

ALERT_GROUP = re.compile(r"IRM alert group: `([A-Z0-9]+)`")
RESOLVED = "✅ Resolved"
ESCALATE = re.compile(r"\b(escalate|opus|dig deeper)\b", re.IGNORECASE)
INVESTIGATE = re.compile(r"\binvestigate\b", re.IGNORECASE)


@dataclass(frozen=True)
class Message:
    thread_id: int
    message_id: int
    parent_id: int | None
    author_id: int
    from_webhook: bool
    from_self: bool
    mentions_self: bool
    content: str


@dataclass(frozen=True)
class NewAlert:
    alert_group_id: str


@dataclass(frozen=True)
class Resolved:
    pass


@dataclass(frozen=True)
class Investigate:
    escalate: bool


@dataclass(frozen=True)
class FollowUp:
    text: str
    escalate: bool


@dataclass(frozen=True)
class Ignore:
    reason: str


Action = NewAlert | Resolved | Investigate | FollowUp | Ignore


def route(
    message: Message,
    *,
    forum_id: int,
    allowed_user_ids: frozenset[int],
    has_session: bool,
) -> Action:
    if message.parent_id != forum_id:
        return Ignore("not in the alerts forum")
    if message.from_self:
        return Ignore("own message")

    if message.from_webhook:
        # A forum post's first message has the same ID as the post itself.
        if message.message_id == message.thread_id:
            match = ALERT_GROUP.search(message.content)
            if match:
                return NewAlert(match.group(1))
            return Ignore("webhook post without an alert group")
        if message.content.startswith(RESOLVED):
            return Resolved()
        return Ignore("status update")

    # Anyone who can post in the server could otherwise steer an agent that
    # reads the hosts; only listed people may.
    if message.author_id not in allowed_user_ids:
        return Ignore("author not allowed")

    escalate = bool(ESCALATE.search(message.content))
    if not has_session:
        if message.mentions_self or INVESTIGATE.search(message.content):
            return Investigate(escalate)
        return Ignore("no investigation in this thread and Hob was not asked")
    return FollowUp(message.content, escalate)


def chunks(text: str, limit: int = 2000) -> list[str]:
    """Split text for Discord's message limit, preferring line breaks."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts
