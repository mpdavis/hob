import logging
import time
from typing import Protocol

from hob import format
from hob.grafana import AlertGroup
from hob.store import Store

log = logging.getLogger(__name__)

TAG_FIRING = "firing"
TAG_RESOLVED = "resolved"


class Chat(Protocol):
    def create_post(self, channel_id: str, name: str, content: str, tag_ids: list[str]) -> str: ...
    def post(self, thread_id: str, content: str) -> None: ...
    def set_tags(self, thread_id: str, tag_ids: list[str]) -> None: ...


class Dispatcher:
    """Turns successive snapshots of Grafana's active alert groups into one
    Discord forum post per group.

    Only ever called with a snapshot that was fetched successfully: an empty
    list resolves everything, so a failed fetch must never reach here as one.
    """

    def __init__(
        self,
        store: Store,
        chat: Chat,
        channel_id: str,
        tags: dict[str, str],
        page_user_id: str | None = None,
        reopen_window: float = 24 * 3600,
        clock=time.time,
    ):
        self.store = store
        self.chat = chat
        self.channel_id = channel_id
        self.tags = tags
        self.page_user_id = page_user_id
        self.reopen_window = reopen_window
        self.clock = clock

        missing = {TAG_FIRING, TAG_RESOLVED} - tags.keys()
        if missing:
            log.warning("forum is missing tags %s; posts go out untagged", sorted(missing))

    def _tag(self, name: str) -> list[str]:
        return [self.tags[name]] if name in self.tags else []

    def reconcile(self, groups: list[AlertGroup]) -> None:
        active = {g.key: g for g in groups}
        for group in groups:
            self._firing(group)
        for thread in self.store.open_threads():
            if thread.group_key not in active:
                self._resolve(thread.group_key, thread.thread_id)

    def _firing(self, group: AlertGroup) -> None:
        now = self.clock()
        fingerprints = {a.fingerprint for a in group.alerts}
        thread = self.store.thread(group.key)

        if thread is None or (
            thread.resolved_at is not None and now - thread.resolved_at > self.reopen_window
        ):
            thread_id = self.chat.create_post(
                self.channel_id,
                format.title(group),
                format.opened(group, self.page_user_id),
                self._tag(TAG_FIRING),
            )
            log.info("opened thread %s for %s", thread_id, group.key)
            self.store.save_thread(group.key, thread_id)
            self.store.set_firing(group.key, fingerprints)
            return

        if thread.resolved_at is not None:
            self.chat.post(thread.thread_id, format.refired(group, self.page_user_id))
            self.chat.set_tags(thread.thread_id, self._tag(TAG_FIRING))
            log.info("re-fired %s in thread %s", group.key, thread.thread_id)
            self.store.save_thread(group.key, thread.thread_id)
            self.store.set_firing(group.key, fingerprints)
            return

        previous = self.store.firing(group.key)
        new = [a for a in group.alerts if a.fingerprint not in previous]
        gone = previous - fingerprints
        if new:
            self.chat.post(thread.thread_id, format.added(new))
        if gone:
            self.chat.post(thread.thread_id, format.partly_resolved(len(gone)))
        if new or gone:
            self.store.set_firing(group.key, fingerprints)

    def _resolve(self, group_key: str, thread_id: str) -> None:
        self.chat.post(thread_id, format.resolved())
        self.chat.set_tags(thread_id, self._tag(TAG_RESOLVED))
        log.info("resolved %s in thread %s", group_key, thread_id)
        self.store.save_thread(group_key, thread_id, resolved_at=self.clock())
        self.store.set_firing(group_key, set())
