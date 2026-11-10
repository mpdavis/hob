import argparse
import logging
import os

from hob.config import Config


def write_secret_file(path: str, env_name: str) -> None:
    """doco-cd hands secrets over as environment variables; ssh wants files."""
    value = os.environ.get(env_name)
    if not value:
        raise SystemExit(f"{env_name} is not set")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value.strip() + "\n")


def run() -> None:
    # Imported here so `hob --help` works without the heavy dependencies.
    from hob.bot import Hob
    from hob.store import Store
    from hob.tools import Tools

    config = Config.from_env()
    write_secret_file(config.ssh_key_path, "HOB_SSH_PRIVATE_KEY")
    write_secret_file(config.ssh_known_hosts_path, "HOB_SSH_KNOWN_HOSTS")
    Hob(config, Store(config.db_path), Tools(config)).run(config.discord_token, log_handler=None)


def main() -> None:
    parser = argparse.ArgumentParser(prog="hob")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="watch the alerts forum and investigate")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "run":
        run()


if __name__ == "__main__":
    main()
