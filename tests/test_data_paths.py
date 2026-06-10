"""OS data-directory and decoy database protection tests."""

from pathlib import Path

import pytest

import data_paths


def test_platform_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    assert data_paths.get_app_data_dir("Windows") == (tmp_path / "programdata" / "SFMS").resolve()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert data_paths.get_app_data_dir("Linux") == (tmp_path / "xdg" / "SFMS").resolve()


def test_configured_paths_are_inside_base_dir():
    import config
    for path in (config.DB_PATH, config.RECEIPTS_DIR, config.REPORTS_DIR, config.BACKUPS_DIR):
        assert Path(path).resolve().is_relative_to(config.BASE_DIR.resolve())


def test_decoy_working_directory_database_is_rejected(tmp_path):
    expected = tmp_path / "real" / "fees_data.db"
    decoy = tmp_path / "cwd" / "fees_data.db"
    decoy.parent.mkdir()
    decoy.touch()
    with pytest.raises(SystemExit):
        data_paths.assert_live_database_path(decoy, expected)
