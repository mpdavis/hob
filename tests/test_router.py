from hob.router import (
    FollowUp,
    Ignore,
    Investigate,
    Message,
    NewAlert,
    Resolved,
    chunks,
    route,
)

FORUM = 100
ME = 7
STRANGER = 8
OPENING = "Disk is 95% full\n\nIRM alert group: `I68T24C13IFW1`\n<https://grafana/irm/x>"


def message(**overrides) -> Message:
    fields = dict(
        thread_id=500,
        message_id=501,
        parent_id=FORUM,
        author_id=ME,
        from_webhook=False,
        from_self=False,
        mentions_self=False,
        content="",
    )
    fields.update(overrides)
    return Message(**fields)


def go(msg: Message, has_session: bool = False):
    return route(msg, forum_id=FORUM, allowed_user_ids=frozenset({ME}), has_session=has_session)


def test_webhook_opening_post_is_a_new_alert():
    msg = message(message_id=500, from_webhook=True, content=OPENING)
    assert go(msg) == NewAlert("I68T24C13IFW1")


def test_webhook_opening_post_without_group_is_ignored():
    msg = message(message_id=500, from_webhook=True, content="something else")
    assert isinstance(go(msg), Ignore)


def test_resolve_status_update():
    assert go(message(from_webhook=True, content="✅ Resolved")) == Resolved()


def test_other_status_updates_are_ignored():
    assert isinstance(go(message(from_webhook=True, content="👀 Acknowledged")), Ignore)


def test_other_channels_and_own_messages_are_ignored():
    assert isinstance(go(message(parent_id=999, mentions_self=True)), Ignore)
    assert isinstance(go(message(from_self=True, mentions_self=True)), Ignore)


def test_strangers_cannot_steer_hob():
    assert isinstance(go(message(author_id=STRANGER, mentions_self=True)), Ignore)
    assert isinstance(go(message(author_id=STRANGER, content="hi"), has_session=True), Ignore)


def test_mention_starts_investigation_when_there_is_none():
    assert go(message(mentions_self=True, content="<@1> look")) == Investigate(escalate=False)
    assert go(message(content="please investigate")) == Investigate(escalate=False)
    assert go(message(mentions_self=True, content="escalate")) == Investigate(escalate=True)


def test_chatter_without_session_or_mention_is_ignored():
    assert isinstance(go(message(content="ugh again")), Ignore)


def test_reply_in_investigated_thread_is_a_follow_up():
    assert go(message(content="what about caddy?"), has_session=True) == FollowUp(
        "what about caddy?", escalate=False
    )
    assert go(message(content="dig deeper"), has_session=True).escalate


def test_chunks_respect_limit_and_prefer_line_breaks():
    text = "\n".join(f"line {i:04d} " + "x" * 40 for i in range(100))
    parts = chunks(text, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(p + "\n" for p in parts).rstrip("\n") == text
    assert all(p.startswith("line ") for p in parts)


def test_chunks_split_long_lines():
    assert chunks("y" * 4500) == ["y" * 2000, "y" * 2000, "y" * 500]
    assert chunks("") == []
