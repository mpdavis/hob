import logging
import time

import httpx

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
MESSAGE_LIMIT = 2000
THREAD_NAME_LIMIT = 100


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DiscordClient:
    """The REST calls the dispatcher needs. Phase 1 only posts, so there is no
    gateway connection."""

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=API,
            headers={"Authorization": f"Bot {token}"},
            timeout=15,
            transport=transport,
        )

    def _request(self, method: str, path: str, attempts: int = 3, **kwargs) -> dict:
        response = self._http.request(method, path, **kwargs)
        for _ in range(attempts - 1):
            if response.status_code != 429:
                break
            retry_after = float(response.json().get("retry_after", 1))
            log.warning("discord rate limited, retrying in %.1fs", retry_after)
            time.sleep(retry_after)
            response = self._http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def forum_tags(self, channel_id: str) -> dict[str, str]:
        channel = self._request("GET", f"/channels/{channel_id}")
        return {t["name"]: t["id"] for t in channel.get("available_tags", [])}

    def create_post(self, channel_id: str, name: str, content: str, tag_ids: list[str]) -> str:
        thread = self._request(
            "POST",
            f"/channels/{channel_id}/threads",
            json={
                "name": truncate(name, THREAD_NAME_LIMIT),
                "applied_tags": tag_ids,
                "message": {
                    "content": truncate(content, MESSAGE_LIMIT),
                    "allowed_mentions": {"parse": ["users"]},
                },
            },
        )
        return thread["id"]

    def post(self, thread_id: str, content: str) -> None:
        self._request(
            "POST",
            f"/channels/{thread_id}/messages",
            json={
                "content": truncate(content, MESSAGE_LIMIT),
                "allowed_mentions": {"parse": ["users"]},
            },
        )

    def set_tags(self, thread_id: str, tag_ids: list[str]) -> None:
        # Posting into an archived thread unarchives it, but a tag change does
        # not, so a re-fire has to say so explicitly.
        self._request(
            "PATCH",
            f"/channels/{thread_id}",
            json={"applied_tags": tag_ids, "archived": False},
        )
