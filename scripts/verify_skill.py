"""Verify that the ZIP boots independently of the source checkout."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from build_skill import build


def main():
    with tempfile.TemporaryDirectory(prefix="screen-bridge-install-test-") as directory:
        root = Path(directory)
        archive = build(root / "screen-bridge-skill.zip")
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            assert "screen-bridge/SKILL.md" in names
            assert "screen-bridge/runtime/uv.lock" in names
            assert not any(".venv/" in name or "__pycache__" in name for name in names)
            bundle.extractall(root / "installed")
        skill = root / "installed" / "screen-bridge"
        for arguments, expected in [
            (["--version"], "0.1.0"),
            (["doctor", "--check-ocr"], '"ready"'),
        ]:
            result = subprocess.run(
                [sys.executable, str(skill / "scripts" / "run.py"), *arguments],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            assert result.returncode == 0, result.stderr
            assert expected in result.stdout, result.stdout
    print("SKILL_PACKAGE_OK")


if __name__ == "__main__":
    main()
