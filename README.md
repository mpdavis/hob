# Hob

The homelab's SRE agent. The design — what Hob is for, what it may touch and
the phases it is built in — is `docs/sre-agent.md` in
[mpdavis/homelab](https://github.com/mpdavis/homelab).

## `hob dispatch`

Phase 1: replaces ntfy. Polls Grafana Cloud's Alertmanager for active alert
groups and keeps one Discord forum post per group (`alertname` + `host`).
New alerts in a group and partial resolves reply in the post; a group
disappearing posts a resolve and swaps the `firing` tag for `resolved`. A
group that fires again within `HOB_REOPEN_WINDOW` reuses its post; later
ones open a new post.

Silenced alerts are skipped. A failed poll changes nothing — only a
successful, empty answer resolves anything.

| Variable | Default | |
|---|---|---|
| `HOB_GRAFANA_URL` | required | e.g. `https://<stack>.grafana.net` |
| `HOB_GRAFANA_TOKEN` | required | Service account token; read access to alerting is enough |
| `HOB_DISCORD_TOKEN` | required | Bot token |
| `HOB_DISCORD_FORUM_CHANNEL_ID` | required | Forum with tags named `firing` and `resolved` |
| `HOB_DISCORD_PAGE_USER_ID` | unset | Mentioned on critical posts, so the phone notifies |
| `HOB_POLL_INTERVAL` | `30` | Seconds |
| `HOB_REOPEN_WINDOW` | `86400` | Seconds |
| `HOB_DB_PATH` | `/data/hob.db` | |
| `HOB_METRICS_PORT` | `9110` | Prometheus metrics |

`hob_dispatch_last_success_timestamp_seconds` advances only after a poll whose
alerts all reached Discord. Grafana alerts when it goes stale and routes that
alert straight to Discord, around Hob.

## Development

Run the suite on the Python the image ships (`Dockerfile` `FROM`):

```sh
uv venv -p 3.14 .venv && uv pip install -p .venv -e ".[dev]" && .venv/bin/pytest -q
```
