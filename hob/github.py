"""Hob's GitHub identity: a GitHub App installed on the homelab repository with
`contents` and `pull_requests` write and no `workflows`. Changes are committed
through the Git Data API rather than pushed from the clone, so the agent never
needs a writable checkout or a shell, and GitHub signs the commits."""

import posixpath
import re
import secrets
import time
from dataclasses import dataclass

import httpx
import jwt

API = "https://api.github.com"
BRANCH_PREFIX = "hob/"
MAX_FILES = 20
MAX_BYTES = 200_000

# Workflows are already out of reach (the App has no `workflows` permission);
# the rest of .github (CODEOWNERS, settings) shapes review, so it is off limits
# too. Nothing under .git is a real path in a tree, but a crafted one could
# confuse a later checkout.
FORBIDDEN_ROOTS = {".github", ".git"}


class ChangeError(ValueError):
    """A proposed change the agent should fix and retry."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str


def check_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        raise ChangeError(f"path {path!r} must be relative to the repository root")
    if posixpath.normpath(path) != path or path.split("/")[0] == "..":
        raise ChangeError(f"path {path!r} is not normalised")
    if path.split("/")[0] in FORBIDDEN_ROOTS:
        raise ChangeError(f"{path}: Hob may not change anything under {path.split('/')[0]}/")
    return path


def apply_changes(changes: list[dict], read) -> dict[str, str | None]:
    """Turn the agent's changes into new file contents, `None` for a deletion.

    `read(path)` returns the file's current text, or None if it does not exist.
    Edits follow the Edit tool's rule: `old_string` must occur exactly once, so
    an edit cannot land somewhere the agent did not look.
    """
    if not changes:
        raise ChangeError("no changes given")
    result: dict[str, str | None] = {}
    for change in changes:
        path = check_path(change.get("path", ""))
        current = result[path] if path in result else read(path)
        if change.get("delete"):
            if current is None:
                raise ChangeError(f"{path}: cannot delete, it does not exist")
            result[path] = None
        elif "content" in change:
            result[path] = change["content"]
        elif "old_string" in change:
            if current is None:
                raise ChangeError(f"{path}: cannot edit, it does not exist")
            old, new = change["old_string"], change.get("new_string", "")
            count = current.count(old) if old else 0
            if count != 1:
                raise ChangeError(
                    f"{path}: old_string occurs {count} times; it must match exactly once"
                )
            result[path] = current.replace(old, new, 1)
        else:
            raise ChangeError(f"{path}: give content, old_string/new_string, or delete")
    if len(result) > MAX_FILES:
        raise ChangeError(f"{len(result)} files changed; the limit is {MAX_FILES}")
    size = sum(len(c.encode()) for c in result.values() if c)
    if size > MAX_BYTES:
        raise ChangeError(f"{size} bytes of content; the limit is {MAX_BYTES}")
    return result


def branch_name(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-")
    return f"{BRANCH_PREFIX}{slug or 'change'}-{secrets.token_hex(3)}"


class GitHub:
    def __init__(
        self,
        *,
        app_id: int,
        installation_id: int,
        private_key: str,
        repo: str,
        base: str,
        http: httpx.AsyncClient | None = None,
    ):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key = private_key
        self.repo = repo
        self.base = base
        self.http = http or httpx.AsyncClient(timeout=30)
        self._token: str | None = None
        self._token_expires = 0.0

    async def _installation_token(self) -> str:
        if self._token and time.time() < self._token_expires - 300:
            return self._token
        now = int(time.time())
        app_jwt = jwt.encode(
            # Backdated a minute for clock drift; GitHub caps the lifetime at 10.
            {"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)},
            self.private_key,
            algorithm="RS256",
        )
        response = await self.http.post(
            f"{API}/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}", **self._headers()},
            # Narrower than the installation, so a token that leaks from a run
            # is good for this one repository and these two permissions only.
            json={
                "repositories": [self.repo.split("/", 1)[1]],
                "permissions": {"contents": "write", "pull_requests": "write"},
            },
        )
        response.raise_for_status()
        token: str = response.json()["token"]
        self._token = token
        # Installation tokens last an hour.
        self._token_expires = time.time() + 3600
        return token

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = await self._installation_token()
        headers = {"Authorization": f"Bearer {token}", **self._headers(), **kwargs.pop("headers", {})}
        return await self.http.request(method, f"{API}/repos/{self.repo}{path}", headers=headers, **kwargs)

    async def _json(self, method: str, path: str, **kwargs):
        response = await self._call(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(
                f"GitHub {method} {path} failed ({response.status_code}): {response.text[:500]}"
            )
        return response.json()

    async def _commit(self, parent: str, changes: list[dict], message: str) -> str:
        commit = await self._json("GET", f"/git/commits/{parent}")
        listing = await self._json("GET", f"/git/trees/{commit['tree']['sha']}", params={"recursive": "1"})
        modes = {entry["path"]: entry["mode"] for entry in listing["tree"] if entry["type"] == "blob"}

        async def read(path: str) -> str | None:
            response = await self._call(
                "GET",
                f"/contents/{path}",
                params={"ref": parent},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
            if response.status_code == 404:
                return None
            if response.is_error:
                raise RuntimeError(f"reading {path} failed ({response.status_code})")
            return response.text

        current = {}
        for change in changes:
            path = check_path(change.get("path", ""))
            if path not in current:
                current[path] = await read(path)
            if modes.get(path) == "120000":
                raise ChangeError(f"{path} is a symlink; change its target instead")
        files = apply_changes(changes, current.get)

        tree = [
            {
                "path": path,
                # Keeps a script's executable bit; new files are plain.
                "mode": modes.get(path, "100644"),
                "type": "blob",
                **({"sha": None} if content is None else {"content": content}),
            }
            for path, content in files.items()
        ]
        new_tree = await self._json(
            "POST", "/git/trees", json={"base_tree": commit["tree"]["sha"], "tree": tree}
        )
        new_commit = await self._json(
            "POST",
            "/git/commits",
            json={"message": message, "tree": new_tree["sha"], "parents": [parent]},
        )
        return new_commit["sha"]

    async def open_pull_request(self, title: str, body: str, changes: list[dict]) -> PullRequest:
        ref = await self._json("GET", f"/git/ref/heads/{self.base}")
        sha = await self._commit(ref["object"]["sha"], changes, title)
        branch = branch_name(title)
        await self._json("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
        pr = await self._json(
            "POST", "/pulls", json={"title": title, "head": branch, "base": self.base, "body": body}
        )
        return PullRequest(pr["number"], pr["html_url"], branch)

    async def update_pull_request(self, number: int, message: str, changes: list[dict]) -> PullRequest:
        pr = await self._json("GET", f"/pulls/{number}")
        branch = pr["head"]["ref"]
        # Only branches Hob opened: never push onto a human's or Renovate's.
        if (
            pr["state"] != "open"
            or pr["head"]["repo"]["full_name"] != self.repo
            or not branch.startswith(BRANCH_PREFIX)
        ):
            raise ChangeError(f"#{number} is not an open pull request on a {BRANCH_PREFIX} branch")
        sha = await self._commit(pr["head"]["sha"], changes, message)
        await self._json("PATCH", f"/git/refs/heads/{branch}", json={"sha": sha, "force": False})
        return PullRequest(number, pr["html_url"], branch)
