import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("tv_fixtures", Path(__file__).with_name("prepare_tv_fixtures.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def test_encoders_are_bounded_and_never_overwrite(tmp_path):
    for flags in ({}, {"uhd": True}, {"uhd": True, "hdr": True}):
        cmd = fixtures.encode_command(tmp_path / "test.mkv", **flags)
        assert "-n" in cmd and "-y" not in cmd
        assert cmd[cmd.index("-t") + 1] == "12"
        assert cmd[cmd.index("-threads") + 1] == "2"
        assert "language=und" in cmd
        if flags.get("hdr"):
            assert "smpte2084" in cmd
            assert any("zscale=" in part for part in cmd)


def test_existing_unowned_fixture_directory_is_not_overwritten(settings, tmp_path):
    settings.store = tmp_path
    root = tmp_path / "downloads" / "films" / fixtures.DIRECTORY
    root.mkdir(parents=True)
    existing = root / "original"
    existing.write_text("keep")
    with pytest.raises(ValueError, match="WITHOUT_MANIFEST"):
        fixtures.prepare(settings)
    assert existing.read_text() == "keep"


def test_fixture_symlink_escape_is_rejected(settings, tmp_path):
    settings.store = tmp_path / "store"
    settings.store.mkdir()
    (settings.store / "downloads").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="UNSAFE_FIXTURE_DIRECTORY"):
        fixtures.prepare(settings)
