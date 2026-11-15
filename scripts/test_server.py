"""Validate source snapshot boundaries without creating VMs or changing a host."""

from pathlib import Path
import tarfile
import tempfile
import unittest

import server


class SourceSnapshotTests(unittest.TestCase):
    """Keep uncommitted source while excluding credentials, run logs and binaries."""

    def test_snapshot_preserves_source_and_excludes_private_artifacts(self):
        """Check the actual archive as well as its recorded content hashes."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            files = {"cmd/main.go": "package main", "scripts/new.py": "# uncommitted",
                     ".env": "secret", ".env.local": "secret", ".git/config": "private",
                     "results/raw/run/log": "private", "bin/pion-l4s": "binary",
                     "testbed/local.json": "assignment", ".cache/data": "cache"}
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            (root / "scripts/link.py").symlink_to(root / ".env")
            archive = Path(folder) / "snapshot.tar.gz"
            hashes = server.snapshot(root, archive)
            expected = {"cmd/main.go", "scripts/new.py"}
            self.assertEqual(set(hashes), expected)
            with tarfile.open(archive) as stream:
                self.assertEqual(set(stream.getnames()), expected)


if __name__ == "__main__":
    unittest.main()
