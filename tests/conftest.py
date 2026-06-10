"""Shared pytest setup for SFMS regression tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fake_hashpw(password: bytes, _salt: bytes) -> bytes:
    return b"$2b$test$" + password


def _fake_checkpw(password: bytes, hashed: bytes) -> bool:
    return hashed == _fake_hashpw(password, b"")


bcrypt = sys.modules.get("bcrypt")
if bcrypt is None:
    if importlib.util.find_spec("bcrypt") is not None:
        import bcrypt as bcrypt_module

        bcrypt = bcrypt_module
    else:
        bcrypt = types.SimpleNamespace()
        sys.modules["bcrypt"] = bcrypt
if not hasattr(bcrypt, "gensalt"):
    bcrypt.gensalt = lambda: b"test-salt"
if not hasattr(bcrypt, "hashpw"):
    bcrypt.hashpw = _fake_hashpw
if not hasattr(bcrypt, "checkpw"):
    bcrypt.checkpw = _fake_checkpw
