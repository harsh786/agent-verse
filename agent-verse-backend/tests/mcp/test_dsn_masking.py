"""NF-4: the masked display form never leaks userinfo, whatever the password holds.

``mongodb://alice:pa#ss@host/db`` used to be masked as ``mongodb://alice:pa``:
the fragment was cut BEFORE the userinfo, so the username and the start of the
password became the "host". Passwords with unencoded ``# @ : / ? %`` (and
percent-encoded ones) are masked to the host list only.
"""

from __future__ import annotations

import re

import pytest

from app.mcp.dsn_secrets import mask_dsn

PASSWORDS = [
    "pa#ss",
    "p@ss",
    "p:a:ss",
    "pa/ss",
    "pa?ss",
    "pa%ss",
    "p%40ss%23x",
    "#@:/?%",
    "@@@",
    "a/b?c#d@e",
    "x?appName=y",
]


@pytest.mark.parametrize("password", PASSWORDS)
@pytest.mark.parametrize(
    "tail",
    ["8.8.8.8:27017/shop", "8.8.8.8:27017,8.8.4.4:27018/shop?replicaSet=rs0", "h.example.com"],
)
def test_no_userinfo_survives(password: str, tail: str) -> None:
    masked = mask_dsn(f"mongodb://alice:{password}@{tail}")
    assert "alice" not in masked
    for piece in (
        password,
        *[p for p in re.split(r"[#@:/?%]", password) if len(p) > 1],
    ):
        if piece not in tail:
            assert piece not in masked, (password, masked)
    assert masked.startswith("mongodb://" + tail.split("/")[0].split("?")[0])


def test_srv_and_options_are_kept_and_secrets_redacted() -> None:
    masked = mask_dsn(
        "mongodb+srv://u:p#w@cluster0.x.mongodb.net/db?retryWrites=true&password=zz;appName=a"
    )
    assert masked == (
        "mongodb+srv://cluster0.x.mongodb.net/db?retryWrites=true&password=<redacted>&appName=a"
    )


def test_unparseable_userinfo_fails_closed() -> None:
    assert mask_dsn("mongodb://alice:secret@/db") == "mongodb://<redacted>"


def test_no_userinfo_is_unchanged() -> None:
    assert mask_dsn("mongodb://8.8.8.8:27017/shop?authSource=admin") == (
        "mongodb://8.8.8.8:27017/shop?authSource=admin"
    )
