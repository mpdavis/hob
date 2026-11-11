import asyncio

import httpx

from hob.config import Config
from hob.store import Store
from hob.tools import Tools, failing_endpoints


def config(**overrides) -> Config:
    fields = dict(
        discord_token="d",
        forum_channel_id=1,
        allowed_user_ids=frozenset({2}),
        grafana_url="https://grafana.example",
        grafana_token="viewer",
        prometheus_uid="prom",
        loki_uid="logs",
        gatus_url="https://status.example",
        hosts={"apps": "10.0.1.55", "infra": "10.0.1.58"},
        ssh_key_path="/k",
        ssh_known_hosts_path="/kh",
        repo_url="https://example/repo.git",
        repo_dir="/repo",
        model="m",
        escalation_model="e",
        debounce=0,
        max_turns=5,
        max_budget_usd=1,
        daily_budget_usd=5,
        db_path=":memory:",
    )
    fields.update(overrides)
    return Config(**fields)


def tools_with(handler) -> Tools:
    return Tools(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def text(result: dict) -> str:
    return result["content"][0]["text"]


def test_prometheus_instant_query_goes_through_datasource_proxy():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.params["query"]
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"status": "success", "data": {"result": [1]}})

    result = asyncio.run(tools_with(handler).prometheus({"query": "up"}))
    assert text(result) == '{"result":[1]}'
    assert seen == {
        "path": "/api/datasources/proxy/uid/prom/api/v1/query",
        "query": "up",
        "auth": "Bearer viewer",
    }


def test_prometheus_range_and_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/query_range")
        assert float(request.url.params["end"]) - float(request.url.params["start"]) == 3600
        return httpx.Response(400, json={"error": "bad"})

    result = asyncio.run(tools_with(handler).prometheus({"query": "up", "range": "1h"}))
    assert result["is_error"] and "prometheus query failed" in text(result)

    bad = asyncio.run(tools_with(handler).prometheus({"query": "up", "range": "forever"}))
    assert bad["is_error"]


def test_loki_lines_are_flattened_oldest_first():
    body = {
        "data": {
            "result": [
                {"stream": {"container": "a"}, "values": [["2", "second"], ["1", "first"]]},
                {"stream": {"container": "b"}, "values": [["3", "third"]]},
            ]
        }
    }
    result = asyncio.run(
        tools_with(lambda r: httpx.Response(200, json=body)).loki({"query": '{container="a"}'})
    )
    assert text(result).splitlines() == [
        "container=a | first",
        "container=a | second",
        "container=b | third",
    ]


def test_gatus_summarises_failures():
    statuses = [
        {"group": "core", "name": "ok", "results": [{"success": True}]},
        {"group": "media", "name": "emby", "results": [{"success": False, "errors": ["timeout"]}]},
        {"group": "media", "name": "new", "results": []},
    ]
    assert failing_endpoints(statuses) == "1 of 3 Gatus endpoints failing:\nmedia/emby: timeout"
    assert failing_endpoints(statuses[:1]) == "All 1 Gatus endpoints are passing."


def test_host_command_rejects_unknown_host_and_verb():
    tools = tools_with(lambda r: httpx.Response(500))
    unknown = asyncio.run(tools.host({"host": "pve1", "command": "ps"}))
    assert unknown["is_error"] and "unknown host" in text(unknown)
    verb = asyncio.run(tools.host({"host": "apps", "command": "restart", "args": ["emby"]}))
    assert verb["is_error"] and "unknown command" in text(verb)


def test_server_exposes_the_named_tools():
    tools = tools_with(lambda r: httpx.Response(200))
    server = tools.server()
    assert server["type"] == "sdk" and server["name"] == "hob"
    assert tools.names() == [
        "mcp__hob__prometheus_query",
        "mcp__hob__loki_query",
        "mcp__hob__gatus_failing",
        "mcp__hob__host_command",
    ]


def test_pull_request_tools_only_with_the_github_app():
    with_app = Tools(
        config(github_app_id=1, github_installation_id=2, github_app_key="pem"),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    assert with_app.names()[-2:] == [
        "mcp__hob__open_pull_request",
        "mcp__hob__update_pull_request",
    ]
    without = tools_with(lambda r: httpx.Response(500))
    result = asyncio.run(without.open_pr({"title": "t", "body": "b", "changes": []}))
    assert result["is_error"] and "not configured" in text(result)


def test_store_sessions_and_spend(tmp_path):
    store = Store(str(tmp_path / "hob.db"))
    assert store.session(1) is None
    store.save_session(1, "s1")
    store.save_session(1, "s2")
    assert store.session(1) == "s2"
    store.add_spend("2026-11-10", 0.5)
    store.add_spend("2026-11-10", 0.25)
    assert store.spent("2026-11-10") == 0.75
    assert store.spent("2026-11-11") == 0.0
    store.close()
