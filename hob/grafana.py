import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx

GROUPS_PATH = "/api/alertmanager/grafana/api/v2/alerts/groups"


@dataclass(frozen=True)
class Alert:
    fingerprint: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    generator_url: str = ""


@dataclass(frozen=True)
class AlertGroup:
    labels: dict[str, str]
    alerts: list[Alert] = field(default_factory=list)

    @property
    def key(self) -> str:
        return ",".join(f"{k}={v}" for k, v in sorted(self.labels.items()))

    @property
    def severity(self) -> str:
        levels = {a.labels.get("severity", "warning") for a in self.alerts}
        return "critical" if "critical" in levels else "warning"


def parse_time(value: str) -> datetime:
    # Alertmanager emits RFC 3339 with up to nanosecond precision; datetime
    # takes at most microseconds.
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_groups(payload: list[dict]) -> list[AlertGroup]:
    """Active alerts by notification group. Silenced and inhibited alerts are
    dropped: a silence is a human saying they already know."""
    merged: dict[str, AlertGroup] = {}
    for raw in payload:
        alerts = [
            Alert(
                fingerprint=a["fingerprint"],
                labels=a.get("labels", {}),
                annotations=a.get("annotations", {}),
                starts_at=parse_time(a["startsAt"]),
                generator_url=a.get("generatorURL", ""),
            )
            for a in raw.get("alerts", [])
            if a.get("status", {}).get("state") == "active"
        ]
        if not alerts:
            continue
        # The same group appears once per receiver it routes to; one thread
        # per group regardless.
        group = AlertGroup(labels=raw.get("labels", {}))
        existing = merged.setdefault(group.key, group)
        seen = {a.fingerprint for a in existing.alerts}
        existing.alerts.extend(a for a in alerts if a.fingerprint not in seen)
    return list(merged.values())


class GrafanaClient:
    def __init__(self, url: str, token: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            transport=transport,
        )

    def active_groups(self) -> list[AlertGroup]:
        response = self._http.get(GROUPS_PATH)
        response.raise_for_status()
        return parse_groups(response.json())
