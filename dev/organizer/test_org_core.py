"""Tests für org_core: Projekterkennung (stark/schwach), Cloud-Flag, Links."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from org_core import (ORG_CLOUD_ATTRS, OrgFile, _org_is_link_dir, org_is_project_dir,
                      org_walk)


class TestProjectMarkers(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orgcore_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def mk(self, rel, data="x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
        return p

    def test_strong_marker_alone(self):
        (self.root / "repo" / ".git").mkdir(parents=True)
        self.assertTrue(org_is_project_dir(self.root / "repo"))
        self.mk("rs/Cargo.toml")
        self.assertTrue(org_is_project_dir(self.root / "rs"))
        self.mk("vs/App.sln")
        self.assertTrue(org_is_project_dir(self.root / "vs"))

    def test_weak_marker_without_code_is_not_project(self):
        self.mk("Ablage/requirements.txt")
        self.mk("Ablage/Rechnung.pdf")
        self.assertFalse(org_is_project_dir(self.root / "Ablage"))
        self.mk("Sachen/package.json", "{}")
        self.assertFalse(org_is_project_dir(self.root / "Sachen"))

    def test_weak_marker_with_code_is_project(self):
        self.mk("tool/requirements.txt")
        self.mk("tool/main.py", "print(1)")
        self.assertTrue(org_is_project_dir(self.root / "tool"))

    def test_user_folders_never_project_by_weak_marker(self):
        for name in ("Downloads", "Desktop", "Dokumente", "Documents"):
            self.mk(f"{name}/requirements.txt")
            self.mk(f"{name}/skript.py", "print(1)")
            self.assertFalse(org_is_project_dir(self.root / name), name)

    def test_walk_marks_and_skips(self):
        self.mk("Downloads/requirements.txt")
        self.mk("Downloads/Lebenslauf.pdf")
        (self.root / "Downloads" / "repo" / ".git").mkdir(parents=True)
        self.mk("Downloads/repo/a.py")
        files, projects, _ = org_walk(self.root)
        rel = {f.rel: f for f in files}
        self.assertFalse(rel["Downloads/Lebenslauf.pdf"].in_project)
        self.assertTrue(rel["Downloads/repo/a.py"].in_project)
        self.assertIn("Downloads/repo", projects)
        self.assertTrue(all(f.cloud is False for f in files))

    @unittest.skipIf(not hasattr(os, "symlink"), "keine Symlinks")
    def test_symlinked_dir_not_entered(self):
        outside = Path(tempfile.mkdtemp(prefix="orgcore_out_"))
        try:
            (outside / "geheim.pdf").write_text("x")
            try:
                os.symlink(outside, self.root / "link", target_is_directory=True)
            except OSError:
                self.skipTest("Symlink nicht erlaubt")
            files, _, _ = org_walk(self.root)
            self.assertFalse(any("geheim" in f.rel for f in files))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_link_tag_helper(self):
        class St:
            def __init__(self, tag):
                self.st_reparse_tag = tag

        class E:
            def __init__(self, tag):
                self._t = tag

            def stat(self, follow_symlinks=True):
                return St(self._t)

        self.assertTrue(_org_is_link_dir(E(0xA0000003)))       # Junction
        self.assertTrue(_org_is_link_dir(E(0xA000000C)))       # Symlink
        self.assertFalse(_org_is_link_dir(E(0x9000001A)))      # OneDrive-Platzhalter
        self.assertFalse(_org_is_link_dir(E(0)))

    def test_cloud_attr_constant(self):
        self.assertTrue(ORG_CLOUD_ATTRS & 0x400000)
        f = OrgFile(path=Path("x"), rel="x", size=1, mtime=0, kind="document", ext=".pdf")
        self.assertFalse(f.cloud)


class TestCloudSkipped(unittest.TestCase):
    def test_dupes_skip_cloud(self):
        from org_dupes import org_find_duplicates
        root = Path(tempfile.mkdtemp(prefix="orgcloud_"))
        try:
            data = os.urandom(5000)
            files = []
            for i in range(3):
                p = root / f"f{i}.bin"
                p.write_bytes(data)
                files.append(OrgFile(path=p, rel=p.name, size=len(data), mtime=0,
                                     kind="other", ext=".bin", cloud=(i == 2)))
            groups = org_find_duplicates(files)
            self.assertEqual(len(groups), 1)
            self.assertEqual({f.name for f in groups[0]}, {"f0.bin", "f1.bin"})
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    print(f"org_core: {r.testsRun} Tests, {len(r.failures) + len(r.errors)} Fehler")
    sys.exit(0 if r.wasSuccessful() else 1)
