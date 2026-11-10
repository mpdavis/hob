import json

import httpx
import pytest

from hob import discord
from hob.discord import DiscordClient
from hob.grafana import GROUPS_PATH, GrafanaClient, parse_groups, parse_time


def raw_alert(fp: str, state: str = "active") -> dict:
    return {
        "fingerprint": fp,
        "labels": {"alertname": "DiskFull", "host": "apps", "severity": "critical"},
        "annotations": {"summary": "disk is full"},
        "startsAt": "2026-11-10T12:00:00.123456789Z",
        "generatorURL": "https://grafana.example/alerting/x",
        "status": {"state": state},
    }


def test_parse_time_accepts_nanoseconds():
    parsed = parse_time("2026-11-10T12:00:00.123456789Z")
    assert parsed.microsecond == 123456
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_groups_drops_silenced_and_merges_receivers():
    payload = [
        {
            "labels": {"alertname": "DiskFull", "host": "apps"},
            "receiver": {"name": "ntfy-critical"},
            "alerts": [raw_alert("a"), raw_alert("b", state="suppressed")],
        },
        {
            "labels": {"alertname": "DiskFull", "host": "apps"},
            "receiver": {"name": "discord-fallback"},
            "alerts": [raw_alert("a")],
        },
        {
            "labels": {"alertname": "Quiet", "host": "infra"},
            "receiver": {"name": "ntfy-warning"},
            "alerts": [raw_alert("c", state="suppressed")],
        },
    ]
    groups = parse_groups(payload)
    assert len(groups) == 1
    assert groups[0].key == "alertname=DiskFull,host=apps"
    assert [a.fingerprint for a in groups[0].alerts] == ["a"]
    assert groups[0].severity == "critical"


def test_grafana_client_sends_token_and_raises_on_error():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["path"] = request.url.path
        return httpx.Response(503)

    client = GrafanaClient("https://grafana.example", "tok", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.active_groups()
    assert seen == {"auth": "Bearer tok", "path": GROUPS_PATH}


def test_discord_retries_rate_limit(monkeypatch):
    monkeypatch.setattr(discord.time, "sleep", lambda _: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 0.1})
        return httpx.Response(201, json={"id": "thread-1"})

    client = DiscordClient("tok", transport=httpx.MockTransport(handler))
    assert client.create_post("forum", "x" * 150, "y" * 3000, ["t"]) == "thread-1"

    assert len(calls) == 2
    body = json.loads(calls[1].content)
    assert len(body["name"]) == discord.THREAD_NAME_LIMIT
    assert len(body["message"]["content"]) == discord.MESSAGE_LIMIT
    assert body["applied_tags"] == ["t"]
    assert calls[1].headers["Authorization"] == "Bot tok"


def test_discord_gives_up_after_repeated_rate_limits(monkeypatch):
    monkeypatch.setattr(discord.time, "sleep", lambda _: None)
    client = DiscordClient(
        "tok", transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"retry_after": 0}))
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.post("thread-1", "hi")


def test_forum_tags_maps_names_to_ids():
    channel = {"available_tags": [{"id": "1", "name": "firing"}, {"id": "2", "name": "resolved"}]}
    client = DiscordClient("tok", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=channel)))
    assert client.forum_tags("forum") == {"firing": "1", "resolved": "2"}
