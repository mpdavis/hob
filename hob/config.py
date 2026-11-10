import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    grafana_url: str
    grafana_token: str
    discord_token: str
    forum_channel_id: str
    page_user_id: str | None
    poll_interval: float
    reopen_window: float
    db_path: str
    metrics_port: int

    @classmethod
    def from_env(cls) -> "Config":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                raise SystemExit(f"{name} is not set")
            return value

        return cls(
            grafana_url=required("HOB_GRAFANA_URL").rstrip("/"),
            grafana_token=required("HOB_GRAFANA_TOKEN"),
            discord_token=required("HOB_DISCORD_TOKEN"),
            forum_channel_id=required("HOB_DISCORD_FORUM_CHANNEL_ID"),
            page_user_id=os.environ.get("HOB_DISCORD_PAGE_USER_ID") or None,
            poll_interval=float(os.environ.get("HOB_POLL_INTERVAL", "30")),
            reopen_window=float(os.environ.get("HOB_REOPEN_WINDOW", str(24 * 3600))),
            db_path=os.environ.get("HOB_DB_PATH", "/data/hob.db"),
            metrics_port=int(os.environ.get("HOB_METRICS_PORT", "9110")),
        )
