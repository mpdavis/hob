import asyncio
import base64
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from hob.github import ChangeError, GitHub, apply_changes, branch_name, check_path

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()

FILES = {
    "docker/infra/hob/compose.yaml": "mem_limit: 1g\ncpus: 1\n",
    "scripts/run.sh": "#!/bin/sh\necho hi\n",
}


@pytest.mark.parametrize(
    "path",
    ["", "/etc/passwd", "../x", "a/../../x", "a//b", "./a", ".github/workflows/x.yml",
     ".github/CODEOWNERS", ".git/config", "a\\b"],
)
def test_check_path_rejects(path):
    with pytest.raises(ChangeError):
        check_path(path)


def test_check_path_allows_dotfiles_elsewhere():
    assert check_path("docker/infra/hob/.doco-cd.yml") == "docker/infra/hob/.doco-cd.yml"


def test_apply_changes_edit_write_delete():
    result = apply_changes(
        [
            {"path": "docker/infra/hob/compose.yaml", "old_string": "1g", "new_string": "1536m"},
            {"path": "docker/infra/hob/compose.yaml", "old_string": "cpus: 1", "new_string": "cpus: 2"},
            {"path": "docs/runbooks/new.md", "content": "# New\n"},
            {"path": "scripts/run.sh", "delete": True},
        ],
        FILES.get,
    )
    assert result == {
        "docker/infra/hob/compose.yaml": "mem_limit: 1536m\ncpus: 2\n",
        "docs/runbooks/new.md": "# New\n",
        "scripts/run.sh": None,
    }


@pytest.mark.parametrize(
    "change,message",
    [
        ({"path": "scripts/run.sh", "old_string": "h", "new_string": "x"}, "occurs 3 times"),
        ({"path": "scripts/run.sh", "old_string": "nope", "new_string": "x"}, "occurs 0 times"),
        ({"path": "missing", "old_string": "a", "new_string": "b"}, "does not exist"),
        ({"path": "missing", "delete": True}, "does not exist"),
        ({"path": "scripts/run.sh"}, "give content"),
    ],
)
def test_apply_changes_errors(change, message):
    with pytest.raises(ChangeError, match=message):
        apply_changes([change], FILES.get)


def test_branch_name():
    name = branch_name("Raise Hob's memory limit to 1.5 GB!")
    assert name.startswith("hob/raise-hob-s-memory-limit-to-1-5-gb-")
    assert len(name) == len("hob/raise-hob-s-memory-limit-to-1-5-gb-") + 6


class FakeGitHub:
    """Just enough of the REST API for one commit and one pull request."""

    def __init__(self, pr_head_ref="hob/fix-abc123"):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.pr_head_ref = pr_head_ref

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))
        repo = "/repos/mpdavis/homelab"
        if path == "/app/installations/2/access_tokens":
            claims = jwt.decode(
                request.headers["Authorization"].split()[1],
                KEY.public_key(),
                algorithms=["RS256"],
            )
            assert claims["iss"] == "1"
            return httpx.Response(201, json={"token": "inst-token"})
        assert request.headers["Authorization"] == "Bearer inst-token"
        if path == f"{repo}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "main-sha"}})
        if path.startswith(f"{repo}/git/commits/"):
            return httpx.Response(200, json={"tree": {"sha": "tree-sha"}})
        if path == f"{repo}/git/trees/tree-sha":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": p, "type": "blob", "mode": "100755" if p.endswith(".sh") else "100644"}
                        for p in FILES
                    ]
                },
            )
        if path.startswith(f"{repo}/contents/"):
            name = path.removeprefix(f"{repo}/contents/")
            if name in FILES:
                return httpx.Response(200, text=FILES[name])
            return httpx.Response(404)
        if path == f"{repo}/git/trees":
            return httpx.Response(201, json={"sha": "new-tree"})
        if path == f"{repo}/git/commits":
            return httpx.Response(201, json={"sha": "new-commit"})
        if path == f"{repo}/git/refs":
            return httpx.Response(201, json={})
        if path == f"{repo}/pulls" and request.method == "POST":
            return httpx.Response(201, json={"number": 7, "html_url": "https://gh/pr/7"})
        if path == f"{repo}/pulls/7":
            return httpx.Response(
                200,
                json={
                    "state": "open",
                    "html_url": "https://gh/pr/7",
                    "head": {
                        "ref": self.pr_head_ref,
                        "sha": "head-sha",
                        "repo": {"full_name": "mpdavis/homelab"},
                    },
                },
            )
        if path.startswith(f"{repo}/git/refs/heads/"):
            return httpx.Response(200, json={})
        return httpx.Response(500, text=f"unexpected {request.method} {path}")


def client(fake: FakeGitHub) -> GitHub:
    return GitHub(
        app_id=1,
        installation_id=2,
        private_key=PEM,
        repo="mpdavis/homelab",
        base="main",
        http=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )


def test_open_pull_request_commits_through_the_api():
    fake = FakeGitHub()
    pr = asyncio.run(
        client(fake).open_pull_request(
            "Raise limit",
            "body",
            [
                {"path": "docker/infra/hob/compose.yaml", "old_string": "1g", "new_string": "1536m"},
                {"path": "scripts/run.sh", "content": "#!/bin/sh\n"},
            ],
        )
    )
    assert pr.number == 7 and pr.branch.startswith("hob/raise-limit-")
    by_path = {(m, p): b for m, p, b in fake.requests}

    token = by_path[("POST", "/app/installations/2/access_tokens")]
    assert token == {
        "repositories": ["homelab"],
        "permissions": {"contents": "write", "pull_requests": "write"},
    }
    tree = by_path[("POST", "/repos/mpdavis/homelab/git/trees")]
    assert tree["base_tree"] == "tree-sha"
    assert {e["path"]: (e["mode"], e["content"]) for e in tree["tree"]} == {
        "docker/infra/hob/compose.yaml": ("100644", "mem_limit: 1536m\ncpus: 1\n"),
        "scripts/run.sh": ("100755", "#!/bin/sh\n"),
    }
    commit = by_path[("POST", "/repos/mpdavis/homelab/git/commits")]
    assert commit == {"message": "Raise limit", "tree": "new-tree", "parents": ["main-sha"]}
    ref = by_path[("POST", "/repos/mpdavis/homelab/git/refs")]
    assert ref == {"ref": f"refs/heads/{pr.branch}", "sha": "new-commit"}
    pull = by_path[("POST", "/repos/mpdavis/homelab/pulls")]
    assert pull == {"title": "Raise limit", "head": pr.branch, "base": "main", "body": "body"}
    # Only one token is minted for the whole run.
    assert sum(1 for m, p, _ in fake.requests if p.endswith("/access_tokens")) == 1


def test_bad_change_commits_nothing():
    fake = FakeGitHub()
    with pytest.raises(ChangeError):
        asyncio.run(
            client(fake).open_pull_request(
                "x", "b", [{"path": ".github/workflows/ci.yml", "content": "pwn"}]
            )
        )
    assert not any(m == "POST" and "/repos/" in p for m, p, _ in fake.requests)


def test_update_builds_on_the_branch_head():
    fake = FakeGitHub()
    pr = asyncio.run(
        client(fake).update_pull_request(
            7, "Also cpus", [{"path": "docker/infra/hob/compose.yaml", "old_string": "cpus: 1", "new_string": "cpus: 2"}]
        )
    )
    assert pr.number == 7
    by_path = {(m, p): b for m, p, b in fake.requests}
    assert by_path[("POST", "/repos/mpdavis/homelab/git/commits")]["parents"] == ["head-sha"]
    assert by_path[("PATCH", "/repos/mpdavis/homelab/git/refs/heads/hob/fix-abc123")] == {
        "sha": "new-commit",
        "force": False,
    }


def test_update_refuses_branches_hob_did_not_open():
    fake = FakeGitHub(pr_head_ref="renovate/caddy-2.x")
    with pytest.raises(ChangeError, match="hob/"):
        asyncio.run(client(fake).update_pull_request(7, "m", [{"path": "a", "content": "b"}]))
    assert not any(m in ("POST", "PATCH") and "/repos/" in p for m, p, _ in fake.requests)
