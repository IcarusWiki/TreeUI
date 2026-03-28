#!/usr/bin/env python3
"""
Rebuild InGameFiles from the installed Icarus data.pak archive.

The extractor clears the existing output directory only after the pak has been
opened successfully, then writes every archive entry using the pak's own
relative directory structure without reshaping it.

Usage:
    python scripts/rebuild_ingamefiles.py
    python scripts/rebuild_ingamefiles.py --pak "D:\\Games\\Steam\\steamapps\\common\\Icarus\\Icarus\\Content\\Data\\data.pak"
    python scripts/rebuild_ingamefiles.py --out ".\\InGameFiles"
    python scripts/rebuild_ingamefiles.py --oodle-dll "C:\\path\\to\\oo2core_9_win64.dll"
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAK = Path(
    r"D:\Games\Steam\steamapps\common\Icarus\Icarus\Content\Data\data.pak"
)
DEFAULT_OUTPUT = ROOT / "InGameFiles"
OODLE_DLL_NAME = "oo2core_9_win64.dll"
PREFERRED_PYTHON_ENV = "ICARUS_WIKI_PREFERRED_PYTHON"
RELAUNCH_GUARD_ENV = "ICARUS_WIKI_REBUILD_INGAMEFILES_RELAUNCHED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clear InGameFiles and repopulate it from Icarus data.pak while "
            "preserving the pak's directory structure."
        )
    )
    parser.add_argument(
        "--pak",
        type=Path,
        default=DEFAULT_PAK,
        help=f"Path to the source pak file (default: {DEFAULT_PAK})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Directory to rebuild from the pak contents (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--aes-key",
        help="Optional AES key for encrypted pak files.",
    )
    parser.add_argument(
        "--oodle-dll",
        type=Path,
        help=(
            "Optional path to oo2core_9_win64.dll. Use this if pyuepak cannot "
            "download the DLL automatically on first run."
        ),
    )
    return parser.parse_args()


def fail(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def resolve_user_path(path: Path, *, base_dir: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = base_dir / expanded
    return expanded.resolve()


def get_preferred_python() -> Path | None:
    override = os.environ.get(PREFERRED_PYTHON_ENV)
    if override:
        candidate = resolve_user_path(Path(override), base_dir=ROOT)
        if candidate.is_file():
            return candidate

    if sys.platform == "win32":
        candidate = Path.home() / "anaconda3" / "python.exe"
        if candidate.is_file():
            return candidate.resolve()

    return None


def ensure_preferred_python() -> None:
    preferred_python = get_preferred_python()
    if preferred_python is None:
        return

    current_python = Path(sys.executable).resolve()
    if current_python == preferred_python:
        return

    if os.environ.get(RELAUNCH_GUARD_ENV) == "1":
        fail(
            "The script was re-launched but is still not running with the preferred "
            f"interpreter.\nCurrent: {current_python}\nPreferred: {preferred_python}"
        )

    print(
        "Re-launching with preferred interpreter:\n"
        f"  current: {current_python}\n"
        f"  target:  {preferred_python}",
        file=sys.stderr,
    )
    env = os.environ.copy()
    env[RELAUNCH_GUARD_ENV] = "1"
    completed = subprocess.run(
        [str(preferred_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        env=env,
    )
    raise SystemExit(completed.returncode)


def resolve_output_dir(path: Path) -> Path:
    output_dir = resolve_user_path(path, base_dir=ROOT)
    if output_dir == Path(output_dir.anchor):
        fail(f"Refusing to clear the filesystem root: {output_dir}")
    if output_dir == ROOT:
        fail(f"Refusing to clear the repository root: {output_dir}")
    return output_dir


def clear_directory_contents(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for child in directory.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def find_pyuepak_dir() -> Path:
    spec = importlib.util.find_spec("pyuepak")
    if spec is None or spec.origin is None:
        preferred_python = get_preferred_python()
        install_python = preferred_python or Path(sys.executable).resolve()
        fail(
            "pyuepak is not installed for this interpreter.\n"
            f"Current interpreter: {Path(sys.executable).resolve()}\n"
            f"Install it with: {install_python} -m pip install -r requirements.txt"
        )
    return Path(spec.origin).resolve().parent


def copy_oodle_dll(oodle_dll: Path | None, pyuepak_dir: Path) -> None:
    if oodle_dll is None:
        return

    source = resolve_user_path(oodle_dll, base_dir=Path.cwd())
    if not source.is_file():
        fail(f"Oodle DLL was not found: {source}")

    destination = pyuepak_dir / OODLE_DLL_NAME
    shutil.copyfile(source, destination)


def remove_pyuepak_spam_log() -> None:
    logger = logging.getLogger("pyuepak")
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.FileHandler):
            continue
        log_path = Path(getattr(handler, "baseFilename", ""))
        if log_path.name.lower() != "spam.log":
            continue
        handler.close()
        logger.removeHandler(handler)
        if log_path.exists():
            log_path.unlink()


def load_pak_class(oodle_dll: Path | None):
    pyuepak_dir = find_pyuepak_dir()
    copy_oodle_dll(oodle_dll, pyuepak_dir)

    try:
        from pyuepak import PakFile
    except Exception as exc:
        fail(
            "Unable to import pyuepak. If this is the first run, pyuepak may need "
            f"to download {OODLE_DLL_NAME}. Re-run with network access or provide "
            f"'--oodle-dll PATH'. Original error: {exc}"
        )

    remove_pyuepak_spam_log()
    return PakFile


def normalize_archive_path(archive_path: str) -> Path:
    text = archive_path.replace("\\", "/").strip()
    if not text:
        fail("Encountered an empty archive path.")
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        fail(f"Refusing to extract a drive-qualified archive path: {archive_path!r}")

    relative = PurePosixPath(text.lstrip("/"))
    parts = []
    for part in relative.parts:
        if part in ("", "."):
            continue
        if part == "..":
            fail(f"Refusing to extract a path that escapes the output dir: {archive_path!r}")
        parts.append(part)

    if not parts:
        fail(f"Encountered an invalid archive path: {archive_path!r}")

    return Path(*parts)


def build_targets(archive_paths: list[str], output_dir: Path) -> list[tuple[str, Path]]:
    targets = []
    for archive_path in archive_paths:
        relative_path = normalize_archive_path(archive_path)
        destination = (output_dir / relative_path).resolve()
        if destination != output_dir and output_dir not in destination.parents:
            fail(f"Refusing to extract outside the output dir: {archive_path!r}")
        targets.append((archive_path, destination))
    return targets


def extract_all(pak, targets: list[tuple[str, Path]]) -> None:
    total = len(targets)
    for index, (archive_path, destination) in enumerate(targets, start=1):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(pak.read_file(archive_path))
        if index == total or index % 25 == 0:
            print(f"  Extracted {index}/{total}: {archive_path}")


def main() -> None:
    ensure_preferred_python()
    args = parse_args()
    pak_path = resolve_user_path(args.pak, base_dir=ROOT)
    output_dir = resolve_output_dir(args.out)

    if not pak_path.is_file():
        fail(f"Pak file was not found: {pak_path}")

    PakFile = load_pak_class(args.oodle_dll)

    print(f"Opening pak: {pak_path}")
    pak = PakFile()
    if args.aes_key:
        pak.set_key(args.aes_key)
    pak.read(str(pak_path))

    archive_paths = pak.list_files()
    if not archive_paths:
        fail(f"No files were found in pak: {pak_path}")

    targets = build_targets(archive_paths, output_dir)

    print(f"Found {len(targets)} files.")
    print(f"Clearing output directory: {output_dir}")
    clear_directory_contents(output_dir)

    print(f"Extracting into: {output_dir}")
    extract_all(pak, targets)
    print("InGameFiles rebuild complete.")


if __name__ == "__main__":
    main()
