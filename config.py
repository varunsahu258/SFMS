"""Application-wide constants for the SFMS desktop application."""

import os

DB_PATH = "fees_data.db"
RECEIPTS_DIR = "receipts/"
REPORTS_DIR = "reports/"
BACKUPS_DIR = "backups/"
APP_VERSION = "2.0"
SESSION_TIMEOUT_DEFAULT = 15  # minutes
BACKUP_INTERVAL_DEFAULT = 6  # hours

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
DEFAULT_ADMIN_ROLE = "ADMIN"
DEFAULT_ADMIN_ACTIVE = 1

SCHOOL_NAME = "Sanskriti Vidhya Mandir High School"
SCHOOL_ADDRESS = "Bareli (Raisen) M.P."
LOGO_PATH = ""

SETTING_SCHOOL_NAME = "school_name"
SETTING_SCHOOL_ADDRESS = "school_address"
SETTING_LOGO_PATH = "logo_path"
SETTING_SESSION_TIMEOUT_MINUTES = "session_timeout_minutes"
SETTING_BACKUP_INTERVAL_HOURS = "backup_interval_hours"

ROLE_ADMIN = "ADMIN"
ROLE_ACCOUNTANT = "ACCOUNTANT"
REGISTER_BIG = "BIG"
REGISTER_SMALL = "SMALL"
REGISTER_BOTH = "BOTH"
STATUS_ACTIVE = "ACTIVE"
CHEQUE_STATUS_PENDING = "PENDING"

APP_TITLE = "SFMS"
APP_SUBTITLE = "School Fees Management System"
SPLASH_BG = "#1a1a2e"
SPLASH_FG = "#ffffff"
SPLASH_DURATION_MS = 2000

RECEIPT_PREFIX = "RCP"
RECEIPT_SEPARATOR = "-"
RECEIPT_SEQUENCE_WIDTH = 6
CURRENCY_PREFIX = "Rs."
DATE_FORMAT = "%d-%m-%Y"
DATETIME_FORMAT = "%d-%m-%Y %H:%M:%S"
ACADEMIC_YEAR_START_MONTH = 4

ACTION_DISCOUNT_CREATED = "DISCOUNT_CREATED"
ACTION_EXEMPTION_CREATED = "EXEMPTION_CREATED"
TAMPER_ACTION_PREFIX = "TAMPER_"

TRG_PAYMENTS_DELETE_MSG = "payments cannot be deleted"
TRG_PAYMENTS_UPDATE_MSG = "payments cannot be updated"
TRG_AUDIT_DELETE_MSG = "audit log cannot be deleted"
TRG_AUDIT_UPDATE_MSG = "audit log cannot be updated"
TRG_RECEIPTS_DELETE_MSG = "receipts cannot be deleted"
TRG_HASH_DELETE_MSG = "receipt hashes cannot be deleted"
TRG_HASH_UPDATE_MSG = "receipt hashes cannot be updated"

for directory in (RECEIPTS_DIR, REPORTS_DIR, BACKUPS_DIR):
    os.makedirs(directory, exist_ok=True)
