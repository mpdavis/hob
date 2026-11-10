"""Read-only commands on the homelab hosts.

The host side is a forced command (`hob-action`, installed by the `hob_access`
Ansible role in mpdavis/homelab) that receives SSH_ORIGINAL_COMMAND and runs
only the verbs below. This module checks the same allowlist before connecting,
so a bad call fails with a useful error instead of a bare rejection, but the
host-side check is the one that matters.
"""

import asyncio
import re

# verb -> (required args, optional args)
VERBS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ps": ((), ()),
    "logs": (("container",), ("since", "tail")),
    "inspect": (("container",), ()),
    "df": ((), ()),
    "journal": (("unit",), ("since",)),
}

# Arguments travel inside SSH_ORIGINAL_COMMAND, which the host splits on
# whitespace. Nothing here can carry a space, quote or shell metacharacter.
ARG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")

OUTPUT_LIMIT = 20_000


def build(verb: str, args: list[str]) -> str:
    if verb not in VERBS:
        raise ValueError(f"unknown command {verb!r}; allowed: {', '.join(sorted(VERBS))}")
    required, optional = VERBS[verb]
    if not len(required) <= len(args) <= len(required) + len(optional):
        names = " ".join([*required, *(f"[{o}]" for o in optional)])
        raise ValueError(f"usage: {verb} {names}".rstrip())
    for arg in args:
        if not ARG.match(arg):
            raise ValueError(f"argument {arg!r} is not allowed")
    return " ".join([verb, *args])


async def run(
    address: str,
    verb: str,
    args: list[str],
    key_path: str,
    known_hosts_path: str,
    timeout: float = 60,
) -> str:
    command = build(verb, args)
    process = await asyncio.create_subprocess_exec(
        "ssh",
        "-i", key_path,
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", f"UserKnownHostsFile={known_hosts_path}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=10",
        f"hob@{address}",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{command!r} timed out after {timeout:.0f}s") from None
    text = out.decode(errors="replace")
    if process.returncode != 0:
        raise RuntimeError(f"{command!r} exited {process.returncode}: {text[-2000:]}")
    if len(text) > OUTPUT_LIMIT:
        # The tail of a log is where the failure is.
        text = f"[truncated to the last {OUTPUT_LIMIT} characters]\n" + text[-OUTPUT_LIMIT:]
    return text
