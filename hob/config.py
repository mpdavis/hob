import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


def _hosts(value: str) -> dict[str, str]:
    """`apps=10.0.1.55,infra=10.0.1.58` → {"apps": "10.0.1.55", ...}"""
    hosts = {}
    for pair in filter(None, (p.strip() for p in value.split(","))):
        name, _, address = pair.partition("=")
        if not name or not address:
            raise SystemExit(f"HOB_HOSTS entry {pair!r} is not name=address")
        hosts[name] = address
    return hosts


@dataclass(frozen=True)
class Config:
    discord_token: str
    forum_channel_id: int
    allowed_user_ids: frozenset[int]
    grafana_url: str
    grafana_token: str
    prometheus_uid: str
    loki_uid: str
    gatus_url: str
    hosts: dict[str, str]
    ssh_key_path: str
    ssh_known_hosts_path: str
    repo_url: str
    repo_dir: str
    model: str
    escalation_model: str
    debounce: float
    max_turns: int
    max_budget_usd: float
    daily_budget_usd: float
    db_path: str
    # The GitHub App. Without it Hob stays read-only and has no PR tools.
    github_app_id: int | None = None
    github_installation_id: int | None = None
    github_app_key: str | None = None
    github_repo: str = "mpdavis/homelab"
    github_base: str = "main"

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            discord_token=_required("HOB_DISCORD_TOKEN"),
            forum_channel_id=int(_required("HOB_DISCORD_FORUM_CHANNEL_ID")),
            allowed_user_ids=frozenset(
                int(u) for u in _required("HOB_DISCORD_ALLOWED_USER_IDS").split(",") if u.strip()
            ),
            grafana_url=_required("HOB_GRAFANA_URL").rstrip("/"),
            grafana_token=_required("HOB_GRAFANA_TOKEN"),
            prometheus_uid=env("HOB_PROMETHEUS_UID", "grafanacloud-prom"),
            loki_uid=env("HOB_LOKI_UID", "grafanacloud-logs"),
            gatus_url=env("HOB_GATUS_URL", "https://status.mpdavis.com").rstrip("/"),
            hosts=_hosts(_required("HOB_HOSTS")),
            ssh_key_path=env("HOB_SSH_KEY_PATH", "/run/hob/ssh_key"),
            ssh_known_hosts_path=env("HOB_SSH_KNOWN_HOSTS_PATH", "/run/hob/known_hosts"),
            repo_url=env("HOB_REPO_URL", "https://github.com/mpdavis/homelab.git"),
            repo_dir=env("HOB_REPO_DIR", "/data/homelab"),
            model=env("HOB_MODEL", "claude-sonnet-5-5"),
            escalation_model=env("HOB_ESCALATION_MODEL", "claude-opus-5-5"),
            debounce=float(env("HOB_DEBOUNCE_SECONDS", "300")),
            max_turns=int(env("HOB_MAX_TURNS", "40")),
            max_budget_usd=float(env("HOB_MAX_BUDGET_USD", "2")),
            daily_budget_usd=float(env("HOB_DAILY_BUDGET_USD", "10")),
            db_path=env("HOB_DB_PATH", "/data/hob.db"),
            github_app_id=int(env("HOB_GITHUB_APP_ID") or 0) or None,
            github_installation_id=int(env("HOB_GITHUB_INSTALLATION_ID") or 0) or None,
            github_app_key=env("HOB_GITHUB_APP_KEY") or None,
            github_repo=env("HOB_GITHUB_REPO", "mpdavis/homelab"),
            github_base=env("HOB_GITHUB_BASE", "main"),
        )

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_app_id and self.github_installation_id and self.github_app_key)
