from datetime import datetime, timezone

import pytest

from hob.dispatcher import Dispatcher
from hob.grafana import Alert, AlertGroup
from hob.store import Store

TAGS = {"firing": "t-firing", "resolved": "t-resolved"}
START = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


class FakeChat:
    def __init__(self):
        self.posts: list[tuple[str, str, list[str]]] = []
        self.messages: list[tuple[str, str]] = []
        self.tags: dict[str, list[str]] = {}

    def create_post(self, channel_id, name, content, tag_ids):
        thread_id = f"thread-{len(self.posts) + 1}"
        self.posts.append((name, content, tag_ids))
        self.tags[thread_id] = tag_ids
        return thread_id

    def post(self, thread_id, content):
        self.messages.append((thread_id, content))

    def set_tags(self, thread_id, tag_ids):
        self.tags[thread_id] = tag_ids


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def alert(fp: str, severity: str = "warning", summary: str = "") -> Alert:
    return Alert(
        fingerprint=fp,
        labels={"alertname": "DiskFull", "host": "apps", "severity": severity},
        annotations={"summary": summary or f"alert {fp}"},
        starts_at=START,
    )


def group(*alerts: Alert, host: str = "apps") -> AlertGroup:
    return AlertGroup(labels={"alertname": "DiskFull", "host": host}, alerts=list(alerts))


@pytest.fixture
def setup():
    chat, clock, store = FakeChat(), Clock(), Store(":memory:")
    dispatcher = Dispatcher(
        store, chat, "forum", TAGS, page_user_id="42", reopen_window=3600, clock=clock
    )
    yield dispatcher, chat, clock
    store.close()


def test_new_group_opens_one_tagged_post(setup):
    dispatcher, chat, _ = setup
    dispatcher.reconcile([group(alert("a"))])
    dispatcher.reconcile([group(alert("a"))])

    assert len(chat.posts) == 1
    name, content, tags = chat.posts[0]
    assert name == "⚠️ DiskFull · apps"
    assert "alert a" in content
    assert tags == ["t-firing"]
    assert chat.messages == []


def test_each_group_gets_its_own_post(setup):
    dispatcher, chat, _ = setup
    dispatcher.reconcile([group(alert("a")), group(alert("b"), host="infra")])
    assert [p[0] for p in chat.posts] == ["⚠️ DiskFull · apps", "⚠️ DiskFull · infra"]


def test_critical_pages_and_warning_does_not(setup):
    dispatcher, chat, _ = setup
    dispatcher.reconcile([group(alert("a", "critical")), group(alert("b"), host="infra")])
    critical, warning = chat.posts
    assert critical[0].startswith("🔥") and "<@42>" in critical[1]
    assert "<@42>" not in warning[1]


def test_new_alert_in_open_group_replies_in_thread(setup):
    dispatcher, chat, _ = setup
    dispatcher.reconcile([group(alert("a"))])
    dispatcher.reconcile([group(alert("a"), alert("b"))])

    assert len(chat.posts) == 1
    [(thread_id, content)] = chat.messages
    assert thread_id == "thread-1"
    assert content.startswith("**Also firing**")
    assert "alert b" in content and "alert a" not in content


def test_partial_resolve_keeps_thread_firing(setup):
    dispatcher, chat, _ = setup
    dispatcher.reconcile([group(alert("a"), alert("b"))])
    dispatcher.reconcile([group(alert("a"))])

    assert chat.messages[0][0] == "thread-1"
    assert "1 alert in this group resolved" in chat.messages[0][1]
    assert chat.tags["thread-1"] == ["t-firing"]


def test_group_disappearing_resolves_thread(setup):
    dispatcher, chat, _ = setup
    dispatcher.reconcile([group(alert("a"))])
    dispatcher.reconcile([])
    dispatcher.reconcile([])

    assert chat.messages == [("thread-1", "✅ **Resolved**")]
    assert chat.tags["thread-1"] == ["t-resolved"]


def test_refire_within_window_reuses_thread(setup):
    dispatcher, chat, clock = setup
    dispatcher.reconcile([group(alert("a"))])
    dispatcher.reconcile([])
    clock.now += 600
    dispatcher.reconcile([group(alert("a"))])

    assert len(chat.posts) == 1
    assert chat.messages[-1][1].startswith("**Firing again**")
    assert chat.tags["thread-1"] == ["t-firing"]


def test_refire_after_window_opens_new_thread(setup):
    dispatcher, chat, clock = setup
    dispatcher.reconcile([group(alert("a"))])
    dispatcher.reconcile([])
    clock.now += 7200
    dispatcher.reconcile([group(alert("a"))])

    assert len(chat.posts) == 2
    assert chat.tags["thread-1"] == ["t-resolved"]
    assert chat.tags["thread-2"] == ["t-firing"]


def test_state_survives_restart(tmp_path):
    db = str(tmp_path / "hob.db")
    chat = FakeChat()
    for _ in range(2):
        store = Store(db)
        Dispatcher(store, chat, "forum", TAGS).reconcile([group(alert("a"))])
        store.close()
    assert len(chat.posts) == 1


def test_missing_tags_post_untagged():
    chat, store = FakeChat(), Store(":memory:")
    Dispatcher(store, chat, "forum", {}).reconcile([group(alert("a"))])
    store.close()
    assert chat.posts[0][2] == []
