import pytest

from hob.hostcmd import build


@pytest.mark.parametrize(
    "verb,args,expected",
    [
        ("ps", [], "ps"),
        ("df", [], "df"),
        ("logs", ["caddy-public"], "logs caddy-public"),
        ("logs", ["emby", "30m", "200"], "logs emby 30m 200"),
        ("logs", ["emby", "2026-11-10T12:00:00"], "logs emby 2026-11-10T12:00:00"),
        ("inspect", ["doco-cd"], "inspect doco-cd"),
        ("journal", ["docker.service", "1h"], "journal docker.service 1h"),
    ],
)
def test_allowed(verb, args, expected):
    assert build(verb, args) == expected


@pytest.mark.parametrize(
    "verb,args",
    [
        ("restart", ["emby"]),
        ("rm", []),
        ("ps", ["-a"]),
        ("logs", []),
        ("logs", ["a", "b", "c", "d"]),
        ("logs", ["emby; rm -rf /"]),
        ("logs", ["emby", "$(id)"]),
        ("logs", ["emby a"]),
        ("logs", ["-f"]),
        ("inspect", ["../../etc"]),
        ("journal", ["x" * 200]),
    ],
)
def test_rejected(verb, args):
    with pytest.raises(ValueError):
        build(verb, args)
