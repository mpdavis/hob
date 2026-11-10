from hob.grafana import Alert, AlertGroup

ICONS = {"critical": "🔥", "warning": "⚠️"}


def title(group: AlertGroup) -> str:
    name = group.labels.get("alertname", "alert")
    rest = [v for k, v in sorted(group.labels.items()) if k != "alertname"]
    return " · ".join([f"{ICONS[group.severity]} {name}", *rest])


def describe(alert: Alert) -> str:
    text = (
        alert.annotations.get("summary")
        or alert.annotations.get("description")
        or alert.labels.get("alertname", "alert")
    )
    line = f"- {text} (since <t:{int(alert.starts_at.timestamp())}:R>)"
    if alert.generator_url:
        line += f" [rule](<{alert.generator_url}>)"
    return line


def opened(group: AlertGroup, page_user_id: str | None) -> str:
    lines = [describe(a) for a in group.alerts]
    if group.severity == "critical" and page_user_id:
        lines.append(f"<@{page_user_id}>")
    return "\n".join(lines)


def refired(group: AlertGroup, page_user_id: str | None) -> str:
    return "**Firing again**\n" + opened(group, page_user_id)


def added(alerts: list[Alert]) -> str:
    return "**Also firing**\n" + "\n".join(describe(a) for a in alerts)


def partly_resolved(count: int) -> str:
    return f"✅ {count} alert{'s' if count != 1 else ''} in this group resolved; the rest are still firing."


def resolved() -> str:
    return "✅ **Resolved**"
