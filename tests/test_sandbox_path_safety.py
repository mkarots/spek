"""Path-traversal guard for the Docker sandbox (no daemon required)."""

from __future__ import annotations

import pytest

from spek.sandbox.base import SandboxError
from spek.sandbox.docker import DockerSandbox


@pytest.mark.parametrize(
    "bad_path",
    [
        "../etc/passwd",
        "foo/../../etc/passwd",
        "/etc/passwd",
        "/work/../etc/passwd",
        "",
    ],
)
def test_validate_path_rejects_escapes(bad_path: str) -> None:
    with pytest.raises(SandboxError):
        DockerSandbox._validate_path(bad_path)


@pytest.mark.parametrize(
    "good_path,expected",
    [
        ("foo.py", "foo.py"),
        ("a/b/c.txt", "a/b/c.txt"),
        ("./foo.py", "foo.py"),
        ("/work/foo.py", "foo.py"),
        ("/work", "."),
    ],
)
def test_validate_path_accepts_workdir_paths(good_path: str, expected: str) -> None:
    assert DockerSandbox._validate_path(good_path) == expected
