"""ZIP archive expansion for uploads (P1a-3): safe, bounded, honest.

Live P0/P1a: every .zip upload was a 415 "unsupported binary file". Archives are
now expanded member by member with hard limits: a zip bomb (compression ratio,
declared or actual uncompressed size), too many members or too deep nesting is
refused before memory is spent; macOS metadata and directories are ignored;
encrypted, symlinked, unsupported or too-large members are skipped and reported;
member names never keep ``..`` / absolute components.
"""

from __future__ import annotations

import io
import stat
import zipfile

import pytest

from app.ingestion.archive import (
    ArchiveLimits,
    ArchiveMember,
    ArchiveRejectedError,
    ArchiveSkip,
    iter_archive,
    safe_member_path,
)
from app.ingestion.document_text import DocumentParseError


def _zip(files: dict[str, bytes | str], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _limits(**kw: int) -> ArchiveLimits:
    base = {"max_member_bytes": 1024 * 1024, "max_total_bytes": 4 * 1024 * 1024,
            "max_members": 50, "max_depth": 3, "max_ratio": 100}
    base.update(kw)
    return ArchiveLimits(**base)


def _run(data: bytes, limits: ArchiveLimits | None = None
         ) -> tuple[list[ArchiveMember], list[ArchiveSkip]]:
    members: list[ArchiveMember] = []
    skips: list[ArchiveSkip] = []
    for item in iter_archive(data, filename="bundle.zip", limits=limits or _limits()):
        (members if isinstance(item, ArchiveMember) else skips).append(item)
    return members, skips


def test_members_of_mixed_types_are_yielded_with_their_paths() -> None:
    members, skips = _run(_zip({"a/leave.txt": "Earned leave: 1.75 days per month.",
                                "b/contacts.csv": "role,name\nHarbour master,Ines\n",
                                "empty-dir/": ""}))
    assert [(m.path, m.ext) for m in members] == [("a/leave.txt", "txt"),
                                                  ("b/contacts.csv", "csv")]
    assert members[0].data == b"Earned leave: 1.75 days per month."
    assert skips == []


def test_nested_archives_are_expanded_with_the_full_member_path() -> None:
    inner = _zip({"notes/history.md": "# History\nCommissioned in 1987."})
    members, _ = _run(_zip({"archive/2025.zip": inner, "top.txt": "top"}))
    assert sorted(m.path for m in members) == ["archive/2025.zip/notes/history.md", "top.txt"]


def test_nesting_deeper_than_the_limit_is_skipped_and_reported() -> None:
    level3 = _zip({"deep.txt": "deep"})
    level2 = _zip({"l3.zip": level3})
    members, skips = _run(_zip({"l2.zip": level2, "ok.txt": "ok"}), _limits(max_depth=2))
    assert [m.path for m in members] == ["ok.txt"]
    assert [(s.name, "nested" in s.reason) for s in skips] == [("l2.zip/l3.zip", True)]


def test_macos_metadata_and_hidden_files_are_ignored_silently() -> None:
    members, skips = _run(_zip({"__MACOSX/a/._doc.docx": b"\x00\x05\x16\x07",
                                "a/.DS_Store": b"\x00\x00\x00\x01Bud1", "a/doc.txt": "real"}))
    assert [m.path for m in members] == ["a/doc.txt"]
    assert skips == []


@pytest.mark.parametrize(("raw", "safe"), [
    ("../../outside/escape.txt", "outside/escape.txt"),
    ("/etc/passwd", "etc/passwd"),
    ("C:\\Users\\x\\notes.txt", "Users/x/notes.txt"),
    ("a/./b/../c.txt", "a/c.txt"),
])
def test_member_names_are_made_safe(raw: str, safe: str) -> None:
    assert safe_member_path(raw) == safe


def test_traversal_member_is_indexed_under_its_safe_name() -> None:
    members, _ = _run(_zip({"../../outside/escape.txt": "ferry every 40 minutes"}))
    assert [m.path for m in members] == ["outside/escape.txt"]


def test_encrypted_and_symlink_members_are_skipped() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        link = zipfile.ZipInfo("link-to-secret")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "/etc/shadow")
        z.writestr("ok.txt", "fine")
    data = bytearray(buf.getvalue())
    members, skips = _run(bytes(data))
    assert [m.path for m in members] == ["ok.txt"]
    assert [(s.name, "link" in s.reason) for s in skips] == [("link-to-secret", True)]

    enc = _zip({"secret.txt": "x", "open.txt": "y"})
    patched = bytearray(enc)
    # Set the "encrypted" flag bit on the first member (local + central headers).
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        idx = patched.find(sig)
        flag_off = idx + (6 if sig == b"PK\x03\x04" else 8)
        patched[flag_off] |= 0x01
    members, skips = _run(bytes(patched))
    assert [m.path for m in members] == ["open.txt"]
    assert skips[0].name == "secret.txt" and "encrypted" in skips[0].reason


def test_a_zip_bomb_is_refused_by_its_compression_ratio() -> None:
    bomb = _zip({"payload.txt": bytes(8 * 1024 * 1024)})
    with pytest.raises(ArchiveRejectedError, match="ratio"):
        _run(bomb, _limits(max_member_bytes=64 * 1024 * 1024, max_total_bytes=256 * 1024 * 1024))


def test_declared_total_over_the_limit_is_refused_before_reading() -> None:
    data = _zip({f"part-{i}.txt": bytes(range(256)) * 4096 for i in range(5)},
                method=zipfile.ZIP_STORED)  # 1 MiB each, incompressible-ish, ratio 1
    with pytest.raises(ArchiveRejectedError, match="uncompressed") as exc:
        _run(data, _limits(max_member_bytes=2 * 1024 * 1024, max_total_bytes=3 * 1024 * 1024))
    assert exc.value.too_large is True


def test_nested_bombs_are_refused_too() -> None:
    inner = _zip({"payload.txt": bytes(4 * 1024 * 1024)})
    outer = _zip({f"part-{i}.zip": inner for i in range(3)}, method=zipfile.ZIP_STORED)
    with pytest.raises(ArchiveRejectedError):
        _run(outer, _limits(max_member_bytes=64 * 1024 * 1024))


def test_a_member_lying_about_its_size_is_cut_off_while_reading() -> None:
    data = bytearray(_zip({"a.txt": bytes(range(256)) * 2048}, method=zipfile.ZIP_STORED))
    # Shrink the declared size in the central directory (a crafted archive).
    cd = data.find(b"PK\x01\x02")
    data[cd + 24:cd + 28] = (100).to_bytes(4, "little")
    with pytest.raises((ArchiveRejectedError, DocumentParseError)):
        _run(bytes(data), _limits(max_member_bytes=1000))


def test_oversized_but_plausible_member_is_skipped_not_fatal() -> None:
    big = bytes(range(256)) * 8192  # 2 MiB, ratio ~1 when stored
    members, skips = _run(_zip({"big.bin.txt": big, "small.txt": "s"},
                               method=zipfile.ZIP_STORED), _limits())
    assert [m.path for m in members] == ["small.txt"]
    assert skips[0].name == "big.bin.txt" and "limit" in skips[0].reason


def test_too_many_members_is_refused() -> None:
    with pytest.raises(ArchiveRejectedError, match="members"):
        _run(_zip({f"f{i}.txt": "x" for i in range(12)}), _limits(max_members=10))


@pytest.mark.parametrize("data", [b"PK\x03\x04 truncated", b"not a zip at all"])
def test_unreadable_archive_is_a_parse_error(data: bytes) -> None:
    with pytest.raises(DocumentParseError, match="zip"):
        _run(data)
