import argparse
import logging
import signal
import threading
import time

from prometheus_client import Counter, Gauge, start_http_server

from hob.config import Config
from hob.discord import DiscordClient
from hob.dispatcher import Dispatcher
from hob.grafana import GrafanaClient
from hob.store import Store

log = logging.getLogger("hob")

# Grafana alerts on this going stale and routes that alert around Hob, straight
# to Discord. It moves only after a full poll-and-post cycle, so a dispatcher
# that polls fine but cannot reach Discord also counts as down.
LAST_SUCCESS = Gauge(
    "hob_dispatch_last_success_timestamp_seconds",
    "Unix time of the last poll whose alerts were all delivered",
)
FAILURES = Counter("hob_dispatch_failures_total", "Poll cycles that failed")
OPEN_GROUPS = Gauge("hob_dispatch_open_groups", "Alert groups currently firing")


def dispatch(config: Config) -> None:
    grafana = GrafanaClient(config.grafana_url, config.grafana_token)
    discord = DiscordClient(config.discord_token)
    dispatcher = Dispatcher(
        store=Store(config.db_path),
        chat=discord,
        channel_id=config.forum_channel_id,
        tags=discord.forum_tags(config.forum_channel_id),
        page_user_id=config.page_user_id,
        reopen_window=config.reopen_window,
    )
    start_http_server(config.metrics_port)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("dispatching every %ss", config.poll_interval)
    while not stop.is_set():
        try:
            groups = grafana.active_groups()
            dispatcher.reconcile(groups)
        except Exception:
            FAILURES.inc()
            log.exception("dispatch cycle failed")
        else:
            LAST_SUCCESS.set(time.time())
            OPEN_GROUPS.set(len(groups))
        stop.wait(config.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(prog="hob")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("dispatch", help="post Grafana alerts to Discord forum threads")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "dispatch":
        dispatch(Config.from_env())


if __name__ == "__main__":
    main()
