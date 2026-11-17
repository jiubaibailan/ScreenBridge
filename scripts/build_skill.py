"""Build a self-contained skill archive and optionally install it without overwriting files."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path


def build(archive: Path, install: Path | None = None) -> Path:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="screen-bridge-package-") as directory:
        skill = Path(directory) / "screen-bridge"
        shutil.copytree(
            root / "skills" / "screen-bridge",
            skill,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        runtime = skill / "runtime"
        runtime.mkdir()
        for filename in ("pyproject.toml", "uv.lock", ".python-version", "README.md"):
            shutil.copy2(root / filename, runtime / filename)
        shutil.copytree(
            root / "src", runtime / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(skill.rglob("*")):
                if file.is_file():
                    bundle.write(file, file.relative_to(skill.parent).as_posix())
        if install is not None:
            if install.exists():
                raise FileExistsError(
                    f"Skill already exists; review it before replacing: {install}"
                )
            install.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill, install)
    return archive.resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/screen-bridge-skill.zip"))
    parser.add_argument("--install", type=Path)
    args = parser.parse_args()
    print(build(args.output, args.install))


if __name__ == "__main__":
    main()
