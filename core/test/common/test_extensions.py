"""Tests for `extensions` -- the marker-file extension scanner."""

from pathlib import Path

import pytest

from common import extensions as _exts


def _write_marker(folder: Path, content: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "extension.conf").write_text(content)


class TestDiscover:
    def test_skips_core_directory(self, tmp_path):
        # Even when `core/` carries a marker, it's reserved as the
        # OSS base and never reported as an extension. This keeps
        # the scanner from accidentally treating the base as one of
        # its own children if someone drops a marker there.
        _write_marker(tmp_path / "core", "id: core\n")
        _write_marker(tmp_path / "ext1", "id: ext1\n")
        out = list(_exts.discover(tmp_path))
        assert [e.id for e in out] == ["ext1"]

    def test_yields_extension_for_marker_bearing_folders(self, tmp_path):
        _write_marker(tmp_path / "myext",
                      "id: myext\nname: MyExt\n")
        _write_marker(tmp_path / "acme",
                      "id: acme\nname: ACME\n")
        out = list(_exts.discover(tmp_path))
        assert sorted(e.id for e in out) == ["acme", "myext"]
        # `name` comes from the marker; `root` and derived paths
        # point at the umbrella folder + standard subdirs.
        ext = {e.id: e for e in out}["myext"]
        assert ext.name == "MyExt"
        assert ext.root == tmp_path / "myext"
        assert ext.src == tmp_path / "myext" / "src"
        assert ext.test == tmp_path / "myext" / "test"
        assert ext.agents_dir == tmp_path / "myext" / "src" / "agents"

    def test_skips_folders_without_marker(self, tmp_path):
        # A random sibling folder (`docs/`, `data/`, `node_modules/`)
        # isn't an extension. Only the marker signals intent.
        (tmp_path / "docs").mkdir()
        (tmp_path / "data").mkdir()
        out = list(_exts.discover(tmp_path))
        assert out == []

    def test_skips_hidden_and_dunder_folders(self, tmp_path):
        # `.git`, `.venv`, `__pycache__` etc. should never be scanned.
        _write_marker(tmp_path / ".secret", "id: secret\n")
        _write_marker(tmp_path / "_drafts", "id: drafts\n")
        _write_marker(tmp_path / "visible", "id: visible\n")
        out = [e.id for e in _exts.discover(tmp_path)]
        assert out == ["visible"]

    def test_falls_back_to_folder_name_when_marker_lacks_id(self, tmp_path):
        # Empty / id-less marker still counts as an extension. The
        # umbrella folder name is the fallback identifier so an
        # extension always has SOME id without requiring the author
        # to populate every yaml field.
        _write_marker(tmp_path / "myext", "")
        out = list(_exts.discover(tmp_path))
        assert len(out) == 1
        assert out[0].id == "myext"
        assert out[0].name == "myext"

    def test_returns_sorted_for_deterministic_boot_log(self, tmp_path):
        # Discovery order drives plugin / agent registration order,
        # which is observable via `all_plugins()` / `all_agents()`.
        # Sorting makes server-boot logs reproducible across runs.
        for name in ["zeta", "alpha", "mu"]:
            _write_marker(tmp_path / name, f"id: {name}\n")
        out = [e.id for e in _exts.discover(tmp_path)]
        assert out == sorted(out)

    def test_missing_root_yields_nothing(self):
        # A non-existent path is a no-op so callers don't need a
        # pre-check. Important for `eva-cli` running from a
        # directory that doesn't look like a repo root.
        out = list(_exts.discover("/this/path/does/not/exist"))
        assert out == []

    def test_marker_is_dir_not_file_skipped(self, tmp_path):
        # `extension.conf` MUST be a file. A directory of that name
        # shouldn't trigger discovery; we keep the scanner strict so
        # accidental layouts don't get false positives.
        (tmp_path / "weird" / "extension.conf").mkdir(parents=True)
        out = list(_exts.discover(tmp_path))
        assert out == []
