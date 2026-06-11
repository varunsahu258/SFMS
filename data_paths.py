"""Absolute, OS-appropriate SFMS data paths and startup path validation."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import subprocess
from pathlib import Path


def get_app_data_dir(system: str | None = None) -> Path:
    """Return and create the machine data directory for the current OS."""
    system = system or platform.system()
    if system == "Windows":
        root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        path = root / "SFMS"
    elif system == "Darwin":
        path = Path.home() / "Library" / "Application Support" / "SFMS"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        path = root / "SFMS"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def get_app_config_dir(system: str | None = None) -> Path:
    """Return and create the user-specific configuration directory."""
    system = system or platform.system()
    if system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = root / "SFMS"
    elif system == "Darwin":
        path = Path.home() / "Library" / "Preferences" / "SFMS"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        path = root / "SFMS"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def apply_restrictive_acl(path: Path, account: str | None = None) -> None:
    """On Windows, remove inheritance and grant access only to SYSTEM/account."""
    if platform.system() != "Windows":
        return
    account = account or getpass.getuser()
    subprocess.run(["icacls", str(path), "/inheritance:r"], check=True, capture_output=True)
    subprocess.run(
        ["icacls", str(path), "/grant:r", "SYSTEM:(OI)(CI)F", f"{account}:(OI)(CI)F"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["icacls", str(path), "/remove:g", r"BUILTIN\Users"],
        check=True,
        capture_output=True,
    )


def connection_database_path(conn) -> Path:
    """Return the resolved main SQLite filename for an open connection."""
    row = next(row for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    return Path(row[2]).resolve()


def assert_live_database_path(actual_path, expected_path) -> Path:
    """Fail closed if a connection/path points at any DB other than configured DB."""
    actual = Path(actual_path).expanduser().resolve()
    expected = Path(expected_path).expanduser().resolve()
    if actual != expected:
        logging.critical("Refusing unexpected SFMS database path: %s (expected %s)", actual, expected)
        raise SystemExit(f"Unexpected database path: {actual}")
    return actual
