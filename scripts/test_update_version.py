from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from update_version import update_version


REPO = Path(__file__).resolve().parents[1]
SHIPPED_CURRENT = (REPO / "startos/versions/current.ts").read_bytes()
SHIPPED_DOCKERFILE = (REPO / "Dockerfile").read_bytes()


class UpdateVersionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.current_path = self.root / "startos/versions/current.ts"
        self.current_path.parent.mkdir(parents=True)
        self.image_path = self.root / "Dockerfile"
        self.write_fixture()

    def write_fixture(self):
        # Exercise the shipped file's actual layout and notes, while fixing the
        # starting version so these tests survive a later upstream bump.
        current = re.sub(rb"(version:\s*)'[^']+'", rb"\g<1>'1.3.0:6'", SHIPPED_CURRENT, count=1)
        image = re.sub(rb"(?m)^ARG KNUTH_VERSION=[^\r\n]+", b"ARG KNUTH_VERSION=1.3.0", SHIPPED_DOCKERFILE, count=1)
        self.current_path.write_bytes(current)
        self.image_path.write_bytes(image)

    def snapshot(self):
        return self.current_path.read_bytes(), self.image_path.read_bytes()

    def replace_notes(self, value):
        source = self.current_path.read_bytes().decode("utf-8")
        start = source.index("  releaseNotes:")
        end = source.index("  migrations:")
        self.current_path.write_bytes((source[:start] + f"  releaseNotes: {value},\n" + source[end:]).encode("utf-8"))

    def assert_failed_without_writes(self, tag="v1.4.0"):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            update_version(self.root, tag)
        self.assertEqual(self.snapshot(), before)

    def test_shipped_release_notes_and_migrations(self):
        before = self.current_path.read_bytes()
        migrations = before[before.index(b"  migrations:"):]
        self.assertEqual(update_version(self.root, "v1.4.0"), "1.4.0:0")
        after, image = self.snapshot()
        self.assertIn(b"version: '1.4.0:0'", after)
        self.assertIn(b"releaseNotes: 'Upstream v1.4.0.'", after)
        self.assertIn(b"ARG KNUTH_VERSION=1.4.0", image)
        self.assertEqual(after[after.index(b"  migrations:"):], migrations)
        self.assertNotIn(b"sidecar serves", after)

    def test_escaped_quotes_and_concatenated_strings(self):
        self.replace_notes(r''' 'The network\'s settings, and ' + "a \"quoted\" value." ''')
        self.assertEqual(update_version(self.root, "1.4.0"), "1.4.0:0")
        self.assertIn(b"releaseNotes: 'Upstream 1.4.0.'", self.current_path.read_bytes())

    def test_same_upstream_preserves_packaging_revision(self):
        before = self.snapshot()
        self.assertIsNone(update_version(self.root, "v1.3.0"))
        self.assertIsNone(update_version(self.root, "1.3.0"))
        self.assertEqual(self.snapshot(), before)
        self.assertIn(b"version: '1.3.0:6'", before[0])

    def test_second_run_is_idempotent(self):
        update_version(self.root, "v1.4.0")
        before = self.snapshot()
        self.assertIsNone(update_version(self.root, "v1.4.0"))
        self.assertEqual(self.snapshot(), before)

    def test_prerelease_pin_and_downgrade_reset(self):
        source = self.current_path.read_bytes().replace(b"ALLOW_DOWNGRADE = false", b"ALLOW_DOWNGRADE = true")
        self.current_path.write_bytes(source)
        self.assertEqual(update_version(self.root, "v1.4.0-rc.1"), "1.4.0-rc.1:0")
        after, image = self.snapshot()
        self.assertIn(b"ALLOW_DOWNGRADE = false", after)
        self.assertIn(b"ARG KNUTH_VERSION=1.4.0-rc.1", image)

    def test_localized_and_template_notes(self):
        values = [
            "{en_US: 'A, B', nested: {text: \"A \\\"quote\\\"\"}}",
            "`A, multiline\nrelease with an escaped \\`backtick\\``",
            "'A' /* commas, braces } */ + 'B'",
        ]
        for value in values:
            with self.subTest(value=value):
                self.write_fixture()
                self.replace_notes(value)
                self.assertEqual(update_version(self.root, "v1.4.0"), "1.4.0:0")

    def test_malformed_tags_do_not_write_files(self):
        for tag in ["", "v", "v1.4", "v1.4.0:0", "v1.4.0\n", "v1.4.0;touch file", "v1.4.0-", "v1.4.0..1"]:
            with self.subTest(tag=tag):
                self.assert_failed_without_writes(tag)

    def test_missing_notes_does_not_partially_bump_version(self):
        self.current_path.write_bytes(self.current_path.read_bytes().replace(b"releaseNotes:", b"notes:"))
        self.assert_failed_without_writes()

    def test_missing_image_pin_does_not_partially_bump_version(self):
        self.image_path.write_bytes(self.image_path.read_bytes().replace(b"ARG KNUTH_VERSION=", b"ARG OTHER_VERSION="))
        self.assert_failed_without_writes()

    def test_mismatched_image_pin_does_not_write_files(self):
        self.image_path.write_bytes(self.image_path.read_bytes().replace(b"ARG KNUTH_VERSION=1.3.0", b"ARG KNUTH_VERSION=1.2.0"))
        self.assert_failed_without_writes()

    def test_missing_downgrade_declaration_does_not_write_files(self):
        self.current_path.write_bytes(self.current_path.read_bytes().replace(b"const ALLOW_DOWNGRADE", b"const OTHER_FLAG"))
        self.assert_failed_without_writes()

    def test_duplicate_image_pins_do_not_write_files(self):
        self.image_path.write_bytes(self.image_path.read_bytes() + b"\nARG KNUTH_VERSION=1.3.0\n")
        self.assert_failed_without_writes()

    def test_unterminated_notes_do_not_write_files(self):
        self.replace_notes("'unterminated")
        self.assert_failed_without_writes()

    def test_preserves_crlf(self):
        for path in [self.current_path, self.image_path]:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        update_version(self.root, "v1.4.0")
        for content in self.snapshot():
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_cli_rejects_invalid_tag(self):
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/update_version.py"), "v1.4.0:0"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Version update failed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.snapshot(), before)

    def run_wrapper(self, tag):
        git = shutil.which("git")
        bash = Path(git).parent.parent / "bin/bash.exe" if os.name == "nt" and git else Path("/bin/bash")
        if not bash.is_file():
            self.skipTest("Bash is not installed")
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "auto-bump.sh").write_text((REPO / "scripts/auto-bump.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        shutil.copyfile(REPO / "scripts/update_version.py", scripts / "update_version.py")
        stub_bin = self.root / "bin"
        stub_bin.mkdir()
        git_stub = stub_bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$GIT_CALLS"\n', encoding="utf-8", newline="\n")
        python_stub = stub_bin / "python3"
        python_stub.write_text(f'#!/usr/bin/env bash\nexec {shlex.quote(Path(sys.executable).as_posix())} "$@"\n', encoding="utf-8", newline="\n")
        git_stub.chmod(0o755)
        python_stub.chmod(0o755)
        calls = self.root / "git-calls.txt"
        def shell_path(path):
            value = path.as_posix()
            return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value
        environment = dict(os.environ, STUB_BIN=shell_path(stub_bin), GIT_CALLS=shell_path(calls))
        result = subprocess.run([
            str(bash), "--noprofile", "--norc", "-c",
            'PATH="$STUB_BIN:$PATH"; export PATH; exec bash scripts/auto-bump.sh "$1"',
            "auto-bump-test", tag,
        ], cwd=self.root, env=environment, capture_output=True, text=True, timeout=30)
        return result, calls.read_text().splitlines() if calls.exists() else []

    def test_wrapper_commits_matching_files_with_owner_identity(self):
        result, calls = self.run_wrapper("v1.4.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            "config user.name CyberAshven",
            "config user.email 130158492+CyberAshven@users.noreply.github.com",
            "add startos/versions/current.ts Dockerfile",
            "commit -m feat: auto-bump to upstream v1.4.0 (v1.4.0:0)",
            "push origin master",
        ])
        self.assertIn(b"version: '1.4.0:0'", self.current_path.read_bytes())
        self.assertIn(b"ARG KNUTH_VERSION=1.4.0", self.image_path.read_bytes())

    def test_wrapper_same_upstream_never_calls_git(self):
        before = self.snapshot()
        result, calls = self.run_wrapper("v1.3.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_wrapper_invalid_tag_never_calls_git(self):
        before = self.snapshot()
        result, calls = self.run_wrapper("v1.4.0; touch unexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
