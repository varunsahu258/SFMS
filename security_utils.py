"""Shared security helpers for authentication, bootstrap, export, and identity display."""

from __future__ import annotations

import re

GENERIC_LOGIN_FAILURE_MESSAGE = "Invalid username or password. Account may be locked after repeated failures."
MACHINE_AUTHORIZATION_REQUIRED_MESSAGE = "This database was last used on a different machine. Owner authorization required."
BOOTSTRAP_PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 10 characters and include at least one uppercase letter, "
    "one digit, and one special character."
)


def validate_bootstrap_password(password: str) -> tuple[bool, str]:
    """Validate the first-time administrator password policy."""
    value = str(password or "")
    if len(value) < 10:
        return False, BOOTSTRAP_PASSWORD_POLICY_MESSAGE
    if not re.search(r"[A-Z]", value):
        return False, BOOTSTRAP_PASSWORD_POLICY_MESSAGE
    if not re.search(r"\d", value):
        return False, BOOTSTRAP_PASSWORD_POLICY_MESSAGE
    if not re.search(r"[^A-Za-z0-9]", value):
        return False, BOOTSTRAP_PASSWORD_POLICY_MESSAGE
    return True, ""


def mask_aadhaar(value) -> str:
    """Return a masked Aadhaar value preserving only the last four digits."""
    raw = re.sub(r"\D", "", str(value or ""))
    if len(raw) < 4:
        return "XXXX-XXXX-XXXX"
    return "XXXX-XXXX-" + raw[-4:]


def display_aadhaar(raw: str, role: str, context: str = "") -> str:
    """Return the Aadhaar display value permitted for the role/context."""
    if str(role or "").upper() == "ADMIN" and context == "identity_verification":
        return str(raw or "")
    return mask_aadhaar(raw)


_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_excel_cell(value):
    """Sanitize user-controlled text for Excel cells."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    sanitized = value.replace("\x00", "").replace("\r", " ").replace("\n", " ")
    if sanitized.startswith(_FORMULA_PREFIXES):
        return "'" + sanitized
    return sanitized
