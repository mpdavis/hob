"""The tools Hob's agent gets beyond reading the homelab repo. Everything but
the pull-request tools is read-only: the Grafana token is a Viewer, Gatus is
public, and host commands go through the hob-action allowlist. Pull requests
change nothing until a human merges them."""

import json
import time
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from hob import hostcmd
from hob.config import Config
from hob.github import ChangeError, GitHub

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


READ_ONLY = ("prometheus_query", "loki_query", "gatus_failing", "host_command")
PULL_REQUESTS = ("open_pull_request", "update_pull_request")

CHANGES_SCHEMA = {
    "type": "array",
    "description": "Each item changes one file. Edit: path, old_string (must match exactly once, "
    "copied from the file as it is on main) and new_string. Whole file: path and content. "
    "Remove: path and delete: true.",
    "items": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "content": {"type": "string"},
            "delete": {"type": "boolean"},
        },
        "required": ["path"],
    },
}


def provenance(thread_url: str | None) -> str:
    where = f"[this alert thread]({thread_url})" if thread_url else "an alert thread"
    return f"\n\n---\nOpened by Hob from {where}. Review it like any other PR; Hob cannot merge."


class Tools:
    def __init__(
        self,
        config: Config,
        http: httpx.AsyncClient | None = None,
        github: GitHub | None = None,
    ):
        self.config = config
        self.http = http or httpx.AsyncClient(timeout=30)
        if github is None and config.github_enabled:
            github = GitHub(
                app_id=config.github_app_id or 0,
                installation_id=config.github_installation_id or 0,
                private_key=config.github_app_key or "",
                repo=config.github_repo,
                base=config.github_base,
                http=self.http,
            )
        self.github = github

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

    async def open_pr(self, args: dict, thread_url: str | None = None) -> dict:
        if self.github is None:
            return _error("pull requests are not configured")
        try:
            pr = await self.github.open_pull_request(
                args["title"], args["body"] + provenance(thread_url), args["changes"]
            )
        except ChangeError as e:
            return _error(f"nothing was committed: {e}")
        except (httpx.HTTPError, RuntimeError, KeyError) as e:
            return _error(f"opening the pull request failed: {e}")
        return _text(f"Opened #{pr.number}: {pr.url} (branch {pr.branch})")

    async def update_pr(self, args: dict) -> dict:
        if self.github is None:
            return _error("pull requests are not configured")
        try:
            pr = await self.github.update_pull_request(
                int(args["number"]), args["message"], args["changes"]
            )
        except ChangeError as e:
            return _error(f"nothing was committed: {e}")
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as e:
            return _error(f"updating the pull request failed: {e}")
        return _text(f"Pushed to #{pr.number}: {pr.url}")

    def server(self, thread_url: str | None = None):
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
        if self.github is not None:

            async def open_pr(args: dict) -> dict:
                return await self.open_pr(args, thread_url)

            definitions += [
                tool(
                    "open_pull_request",
                    "Open a pull request against main in the homelab repository with the given "
                    "file changes, committed as hob-homelab[bot]. Nothing changes until a human "
                    "merges it. Files under .github/ cannot be changed. The body should say what "
                    "was wrong, the evidence, and what the change does.",
                    {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "changes": CHANGES_SCHEMA,
                        },
                        "required": ["title", "body", "changes"],
                    },
                )(open_pr),
                tool(
                    "update_pull_request",
                    "Add a commit to a pull request you opened earlier (its branch starts with "
                    "hob/). Edits apply to the branch as it is now, including your earlier "
                    "commits, not to main.",
                    {
                        "type": "object",
                        "properties": {
                            "number": {"type": "integer"},
                            "message": {"type": "string"},
                            "changes": CHANGES_SCHEMA,
                        },
                        "required": ["number", "message", "changes"],
                    },
                )(self.update_pr),
            ]
        return create_sdk_mcp_server(SERVER, tools=definitions)

    def names(self) -> list[str]:
        names = READ_ONLY + (PULL_REQUESTS if self.github is not None else ())
        return [f"mcp__{SERVER}__{name}" for name in names]
