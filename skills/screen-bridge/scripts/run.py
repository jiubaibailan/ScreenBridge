"""Portable skill entry point; runs the bundled, locked project with uv."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    skill = Path(__file__).resolve().parents[1]
    project = skill / "runtime"
    if not (project / "pyproject.toml").exists():
        project = skill.parents[1]
    if not (project / "pyproject.toml").exists():
        print(
            "Missing bundled runtime. Install the complete screen-bridge skill ZIP.",
            file=sys.stderr,
        )
        return 1
    uv = shutil.which("uv")
    if not uv:
        print(
            "uv is required: https://docs.astral.sh/uv/getting-started/installation/",
            file=sys.stderr,
        )
        return 1
    return subprocess.call(
        [
            uv,
            "run",
            "--locked",
            "--no-dev",
            "--project",
            str(project),
            "python",
            "-m",
            "screen_bridge",
            *sys.argv[1:],
        ],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


if __name__ == "__main__":
    raise SystemExit(main())
