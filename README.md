# Hob

The homelab's SRE agent. The design — what Hob is for, what it may touch and
the phases it is built in — is `docs/sre-agent.md` in
[mpdavis/homelab](https://github.com/mpdavis/homelab).

## `hob run`

Phases 2 and 3: investigation, and fixes as pull requests. Hob is a Discord bot watching the
`#homelab-alerts` forum, where Grafana IRM opens one post per alert group
(`tofu/grafana/irm.tf` in homelab).

- **New post** (the IRM webhook's, carrying ``IRM alert group: `<id>` ``):
  Hob waits `HOB_DEBOUNCE_SECONDS`, then investigates and replies in the post.
  A `✅ Resolved` from IRM during the wait cancels it
- **`@Hob` or "investigate"** in a post Hob has not looked at starts an
  investigation at once — use this for posts that predate Hob
- **Any reply** in a post Hob has investigated resumes that agent session, so
  follow-ups keep their context. "escalate", "dig deeper" or "opus" switches
  to `HOB_ESCALATION_MODEL` for that turn

Only `HOB_DISCORD_ALLOWED_USER_IDS` can start or steer an investigation.

The agent is the Claude Agent SDK, run on a clone of homelab (synced before
every run) with only `Read`, `Grep` and `Glob` built in, plus tools of its
own:

| Tool | Reaches |
|---|---|
| `prometheus_query`, `loki_query` | Grafana Cloud, through the datasource proxy with a Viewer token |
| `gatus_failing` | Gatus's status API |
| `host_command` | `ssh hob@<host> <verb> <args>`, which the host's `hob-action` forced command allowlists |
| `open_pull_request`, `update_pull_request` | GitHub, as the `hob-homelab` App; only when it is configured |

The pull-request tools take the change as edits (`old_string` → `new_string`,
matched exactly once), whole files or deletions, and commit them through the
Git Data API onto a `hob/…` branch, so the agent never needs a writable
checkout. Each run mints an installation token narrowed to homelab with
`contents` and `pull_requests` write. They refuse paths under `.github/` and
will only add commits to open PRs on `hob/` branches. Merging is the human's.

Everything else Claude Code ships — Bash, Edit, Write, web access — is
absent, and `permission_mode` is `dontAsk`, so anything not allowed is
refused.

### `hob-action` protocol

The host side lives in homelab's `hob_access` Ansible role; `hob/hostcmd.py`
holds the client side. Both must agree:

| Verb | Arguments | Runs |
|---|---|---|
| `ps` | | `docker ps -a` |
| `logs` | `container [since] [tail]` | `docker logs` |
| `inspect` | `container` | `docker inspect` with `Config.Env` removed |
| `df` | | `df -h` |
| `journal` | `unit [since]` | `journalctl -u` |

Arguments match `^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$`.

### Configuration

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Hob's own key, in its own workspace |
| `HOB_DISCORD_TOKEN` | required | Bot token; needs the Message Content intent |
| `HOB_DISCORD_FORUM_CHANNEL_ID` | required | `#homelab-alerts` |
| `HOB_DISCORD_ALLOWED_USER_IDS` | required | Comma-separated |
| `HOB_GRAFANA_URL` | required | |
| `HOB_GRAFANA_TOKEN` | required | Service account token, Viewer role |
| `HOB_HOSTS` | required | `apps=10.0.1.55,infra=10.0.1.58` |
| `HOB_SSH_PRIVATE_KEY` | required | Written to `HOB_SSH_KEY_PATH` at start |
| `HOB_SSH_KNOWN_HOSTS` | required | The hosts' keys; strict checking is on |
| `HOB_PROMETHEUS_UID` / `HOB_LOKI_UID` | `grafanacloud-prom` / `grafanacloud-logs` | |
| `HOB_GATUS_URL` | `https://status.mpdavis.com` | |
| `HOB_MODEL` / `HOB_ESCALATION_MODEL` | `claude-sonnet-5-5` / `claude-opus-5-5` | |
| `HOB_DEBOUNCE_SECONDS` | `300` | |
| `HOB_MAX_TURNS` / `HOB_MAX_BUDGET_USD` | `40` / `2` | Per run |
| `HOB_DAILY_BUDGET_USD` | `10` | Runs are refused once a UTC day reaches it |
| `HOB_REPO_URL` / `HOB_REPO_DIR` | homelab / `/data/homelab` | |
| `HOB_DB_PATH` | `/data/hob.db` | |
| `HOB_GITHUB_APP_ID` / `HOB_GITHUB_INSTALLATION_ID` | unset | Both, plus the key, enable the pull-request tools |
| `HOB_GITHUB_APP_KEY` | unset | The App's private key (PEM) |
| `HOB_GITHUB_REPO` / `HOB_GITHUB_BASE` | `mpdavis/homelab` / `main` | |

`/data` is `HOME`, so the clone, the SQLite state and the SDK's session
transcripts share one volume.

## Development

Run the suite on the Python the image ships (`Dockerfile` `FROM`):

```sh
uv venv -p 3.14 .venv && uv pip install -p .venv -e ".[dev]" && .venv/bin/pytest -q
```
