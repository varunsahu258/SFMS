"""Configurable accountant permissions and non-delegable administrator controls."""

from datetime import datetime
import inspect
import sqlite3

import auth
from migrations import migration_v010_accountant_permissions
from permissions import DEFAULT_ACCOUNTANT_PERMISSIONS, PERMISSION_KEYS
from ui_master_utils import ensure_admin_write, ensure_permission_write


def session(role: str, user_id: int = 1, token: str | None = "token") -> auth.Session:
    now = datetime.now()
    return auth.Session(token, user_id, role.lower(), role, now, now)


def permission_db(tmp_path, monkeypatch):
    path = tmp_path / "permissions.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,role TEXT,is_active INTEGER);
        INSERT INTO users VALUES(1,'admin','ADMIN',1);
        INSERT INTO users VALUES(2,'accountant','ACCOUNTANT',1);
        """
    )
    migration_v010_accountant_permissions(conn)
    conn.commit()
    conn.close()
    monkeypatch.setattr(auth, "DB_PATH", str(path))
    return path


def test_accountant_defaults_preserve_normal_operational_access(tmp_path, monkeypatch):
    permission_db(tmp_path, monkeypatch)
    auth.CURRENT_SESSION = session("ACCOUNTANT", 2)
    try:
        for permission_key in DEFAULT_ACCOUNTANT_PERMISSIONS:
            assert auth.has_permission(permission_key)
        assert not auth.has_permission("void_payments")
        assert not auth.can_override_financial_data()
    finally:
        auth.CURRENT_SESSION = None


def test_explicit_permission_overrides_take_effect_immediately(tmp_path, monkeypatch):
    path = permission_db(tmp_path, monkeypatch)
    auth.CURRENT_SESSION = session("ACCOUNTANT", 2)
    try:
        assert auth.has_permission("manage_students")
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO user_permissions VALUES(2,'manage_students',0,'now',1)"
            )
            conn.execute(
                "INSERT INTO user_permissions VALUES(2,'void_payments',1,'now',1)"
            )
        assert not auth.has_permission("manage_students")
        assert auth.has_permission("void_payments")
    finally:
        auth.CURRENT_SESSION = None


def test_admin_has_every_catalog_permission_but_logged_out_user_has_none(tmp_path, monkeypatch):
    permission_db(tmp_path, monkeypatch)
    auth.CURRENT_SESSION = session("ADMIN")
    try:
        assert all(auth.has_permission(key) for key in PERMISSION_KEYS)
        assert ensure_permission_write("void_payments")
        assert ensure_admin_write()
    finally:
        auth.CURRENT_SESSION = None
    assert not any(auth.has_permission(key) for key in PERMISSION_KEYS)


def test_unknown_permissions_and_invalid_sessions_are_denied(tmp_path, monkeypatch):
    permission_db(tmp_path, monkeypatch)
    auth.CURRENT_SESSION = session("ACCOUNTANT", 2, token=None)
    try:
        assert not auth.has_permission("manage_students")
        assert not auth.has_permission("not_a_real_permission")
    finally:
        auth.CURRENT_SESSION = None


def _guarded_permission(callable_obj) -> str:
    """Read the permission key captured by auth.require_permission."""
    return inspect.getclosurevars(callable_obj).nonlocals["permission_key"]


def test_ui_actions_use_specific_permissions_and_security_admin_stays_admin_only():
    from ui_classes import ClassSectionWindow
    from ui_permissions import AccountantPermissionsWindow
    from ui_receipt_reprint import ReprintWindow
    from ui_students import StudentWindow
    from ui_users import UserManagementWindow
    from ui_void_payment import VoidPaymentWindow

    assert _guarded_permission(StudentWindow.__init__) == "manage_students"
    assert _guarded_permission(ClassSectionWindow.__init__) == "manage_classes"
    assert _guarded_permission(ReprintWindow.__init__) == "reprint_receipts"
    assert _guarded_permission(VoidPaymentWindow.__init__) == "void_payments"
    assert inspect.getclosurevars(AccountantPermissionsWindow.__init__).nonlocals["roles"] == ("ADMIN",)
    assert inspect.getclosurevars(UserManagementWindow.__init__).nonlocals["roles"] == ("ADMIN",)
