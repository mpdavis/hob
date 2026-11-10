"""The tools Hob's agent gets beyond reading the homelab repo. All read-only:
the Grafana token is a Viewer, Gatus is public, and host commands go through
the hob-action allowlist."""

import json
import time
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from hob import hostcmd
from hob.config import Config

SERVER = "hob"
OUTPUT_LIMIT = 20_000


def _text(text: str) -> dict[str, Any]:
    if len(text) > OUTPUT_LIMIT:
        text = text[:OUTPUT_LIMIT] + f"\n[truncated at {OUTPUT_LIMIT} characters; narrow the query]"
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict[str, Any]:
    return {**_text(text), "is_error": True}


def _seconds(duration: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if not duration or duration[-1] not in units or not duration[:-1].isdigit():
        raise ValueError(f"duration {duration!r} is not like 30m, 6h or 2d")
    return int(duration[:-1]) * units[duration[-1]]


def failing_endpoints(statuses: list[dict]) -> str:
    failing = []
    for endpoint in statuses:
        results = endpoint.get("results") or []
        if results and not results[-1].get("success", True):
            errors = "; ".join(results[-1].get("errors") or []) or "conditions failed"
            failing.append(f"{endpoint.get('group')}/{endpoint.get('name')}: {errors}")
    if not failing:
        return f"All {len(statuses)} Gatus endpoints are passing."
    return f"{len(failing)} of {len(statuses)} Gatus endpoints failing:\n" + "\n".join(failing)


class Tools:
    def __init__(self, config: Config, http: httpx.AsyncClient | None = None):
        self.config = config
        self.http = http or httpx.AsyncClient(timeout=30)

    async def _grafana(self, uid: str, path: str, params: dict) -> dict:
        response = await self.http.get(
            f"{self.config.grafana_url}/api/datasources/proxy/uid/{uid}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.config.grafana_token}"},
        )
        response.raise_for_status()
        return response.json()

    async def prometheus(self, args: dict) -> dict:
        try:
            if args.get("range"):
                end = time.time()
                body = await self._grafana(
                    self.config.prometheus_uid,
                    "/api/v1/query_range",
                    {
                        "query": args["query"],
                        "start": end - _seconds(args["range"]),
                        "end": end,
                        "step": args.get("step") or "5m",
                    },
                )
            else:
                body = await self._grafana(
                    self.config.prometheus_uid, "/api/v1/query", {"query": args["query"]}
                )
        except (httpx.HTTPError, ValueError) as e:
            return _error(f"prometheus query failed: {e}")
        return _text(json.dumps(body.get("data", body), separators=(",", ":")))

    async def loki(self, args: dict) -> dict:
        try:
            end = time.time()
            body = await self._grafana(
                self.config.loki_uid,
                "/loki/api/v1/query_range",
                {
                    "query": args["query"],
                    "start": int((end - _seconds(args.get("since") or "1h")) * 1e9),
                    "end": int(end * 1e9),
                    "limit": min(int(args.get("limit") or 200), 1000),
                    "direction": "backward",
                },
            )
        except (httpx.HTTPError, ValueError) as e:
            return _error(f"loki query failed: {e}")
        lines = []
        for stream in body.get("data", {}).get("result", []):
            labels = ",".join(f"{k}={v}" for k, v in sorted(stream.get("stream", {}).items()))
            for ts, line in stream.get("values", []):
                lines.append((ts, f"{labels} | {line}"))
        if not lines:
            return _text("no log lines matched")
        lines.sort()
        return _text("\n".join(line for _, line in lines))

    async def gatus(self, args: dict) -> dict:
        try:
            response = await self.http.get(f"{self.config.gatus_url}/api/v1/endpoints/statuses")
            response.raise_for_status()
        except httpx.HTTPError as e:
            return _error(f"gatus request failed: {e}")
        return _text(failing_endpoints(response.json()))

    async def host(self, args: dict) -> dict:
        name = args["host"]
        if name not in self.config.hosts:
            return _error(f"unknown host {name!r}; known: {', '.join(sorted(self.config.hosts))}")
        try:
            out = await hostcmd.run(
                self.config.hosts[name],
                args["command"],
                list(args.get("args") or []),
                self.config.ssh_key_path,
                self.config.ssh_known_hosts_path,
            )
        except (ValueError, RuntimeError, OSError) as e:
            return _error(str(e))
        return _text(out or "(no output)")

    def server(self):
        host_names = sorted(self.config.hosts)
        definitions = [
            tool(
                "prometheus_query",
                "Run a PromQL query against Grafana Cloud's Prometheus. Without `range` it is an "
                "instant query; with `range` (e.g. 1h, 6h, 2d) it is a range query at `step` "
                "(default 5m). Keep result sets small: aggregate and filter by labels.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "range": {"type": "string"},
                        "step": {"type": "string"},
                    },
                    "required": ["query"],
                },
            )(self.prometheus),
            tool(
                "loki_query",
                "Run a LogQL query against Grafana Cloud's Loki, newest lines first, over the last "
                "`since` (default 1h), at most `limit` lines (default 200, max 1000).",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "since": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            )(self.loki),
            tool(
                "gatus_failing",
                "List the Gatus endpoints whose latest check failed, with their errors.",
                {"type": "object", "properties": {}},
            )(self.gatus),
            tool(
                "host_command",
                "Run a read-only command on a homelab host over SSH. Commands: "
                "`ps` (docker ps -a); `logs <container> [since] [tail]` (docker logs, e.g. since "
                "30m, tail 200); `inspect <container>` (docker inspect, environment removed); "
                "`df` (disk usage); `journal <unit> [since]` (journalctl -u). Arguments may not "
                f"contain spaces or shell characters. Hosts: {', '.join(host_names)}.",
                {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "enum": host_names},
                        "command": {"type": "string", "enum": sorted(hostcmd.VERBS)},
                        "args": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["host", "command"],
                },
            )(self.host),
        ]
        return create_sdk_mcp_server(SERVER, tools=definitions)

    @staticmethod
    def names() -> list[str]:
        return [
            f"mcp__{SERVER}__{name}"
            for name in ("prometheus_query", "loki_query", "gatus_failing", "host_command")
        ]
