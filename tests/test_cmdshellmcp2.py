import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cmdshellmcp2


class EditFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.previous_cwd = cmdshellmcp2.cwd
        cmdshellmcp2.cwd = Path(self.temporary_directory.name)
        self.addCleanup(setattr, cmdshellmcp2, "cwd", self.previous_cwd)
        self.previous_edit_delete_backup = cmdshellmcp2.EDIT_DELETE_BACKUP
        cmdshellmcp2.EDIT_DELETE_BACKUP = False
        self.addCleanup(
            setattr,
            cmdshellmcp2,
            "EDIT_DELETE_BACKUP",
            self.previous_edit_delete_backup,
        )

    def write(self, name="example.txt", text="foo\n"):
        path = cmdshellmcp2.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_substitution_creates_backup_and_unified_diff(self):
        source = self.write()

        response = cmdshellmcp2.editFile("example.txt", "s/foo/bar/g")

        self.assertTrue(response.startswith("Success:"))
        self.assertEqual(source.read_text(encoding="utf-8"), "bar\n")
        self.assertEqual((cmdshellmcp2.cwd / "example.txt.bk1").read_text(), "foo\n")
        self.assertIn("Below is the unified diff:", response)
        self.assertIn("-foo\n+bar", response)

    def test_next_backup_is_selected_without_overwriting_existing_backup(self):
        self.write()
        first_backup = self.write("example.txt.bk1", "keep me\n")

        response = cmdshellmcp2.editFile("example.txt", "s/foo/bar/")

        self.assertIn("Backup: example.txt.bk2", response)
        self.assertEqual(first_backup.read_text(), "keep me\n")
        self.assertEqual((cmdshellmcp2.cwd / "example.txt.bk2").read_text(), "foo\n")

    def test_local_path_restrictions_and_missing_file(self):
        self.assertTrue(cmdshellmcp2.editFile("../file", "d").startswith("Error:"))
        self.assertTrue(cmdshellmcp2.editFile("/tmp/file", "d").startswith("Error:"))
        self.assertIn("does not exist", cmdshellmcp2.editFile("missing", "d"))

    def test_invalid_sed_leaves_source_unchanged_and_removes_backup(self):
        source = self.write()

        response = cmdshellmcp2.editFile("example.txt", "s/[unterminated/x/")

        self.assertTrue(response.startswith("Error:"))
        self.assertEqual(source.read_text(), "foo\n")
        self.assertFalse((cmdshellmcp2.cwd / "example.txt.bk1").exists())

    def test_sandbox_rejects_command_execution_and_removes_backup(self):
        source = self.write()

        response = cmdshellmcp2.editFile("example.txt", "s/foo/echo unsafe/e")

        self.assertTrue(response.startswith("Error:"))
        self.assertEqual(source.read_text(), "foo\n")
        self.assertFalse((cmdshellmcp2.cwd / "example.txt.bk1").exists())

    def test_dangerous_or_input_selecting_arguments_are_rejected(self):
        self.write()
        for argument in ("-i", "--in-place", "-e", "--expression", "-f", "--file", "other.txt"):
            with self.subTest(argument=argument):
                response = cmdshellmcp2.editFile("example.txt", "s/foo/bar/", [argument])
                self.assertTrue(response.startswith("Error:"))
        self.assertFalse((cmdshellmcp2.cwd / "example.txt.bk1").exists())

    def test_no_change_is_reported(self):
        self.write()
        response = cmdshellmcp2.editFile("example.txt", "s/not-present/value/")
        self.assertIn("produced no differences", response)

    def test_configured_backup_deletion_happens_after_diff_is_composed(self):
        self.write()
        cmdshellmcp2.EDIT_DELETE_BACKUP = True

        response = cmdshellmcp2.editFile("example.txt", "s/foo/bar/")

        self.assertTrue(response.startswith("Success:"))
        self.assertIn("Backup deleted: example.txt.bk1", response)
        self.assertIn("-foo\n+bar", response)
        self.assertFalse((cmdshellmcp2.cwd / "example.txt.bk1").exists())


class EditFileRegistrationTests(unittest.TestCase):
    @staticmethod
    def tool_names(disabled=None):
        server = cmdshellmcp2.create_server("127.0.0.1", 8003, ["date"], "", disabled)
        return {tool.name for tool in asyncio.run(server.list_tools())}

    def test_edit_file_is_registered(self):
        self.assertIn("editFile", self.tool_names())

    def test_edit_file_can_be_disabled(self):
        self.assertNotIn("editFile", self.tool_names(["editFile"]))


class ApplyPatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.previous_cwd = cmdshellmcp2.cwd
        cmdshellmcp2.cwd = Path(self.temporary_directory.name)
        self.addCleanup(setattr, cmdshellmcp2, "cwd", self.previous_cwd)

    def write(self, name="example.txt", text="old\n"):
        path = cmdshellmcp2.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def diff(old_name="a/example.txt", new_name="b/example.txt"):
        return (
            f"--- {old_name}\n"
            f"+++ {new_name}\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

    def test_applies_unified_diff_with_default_pnum(self):
        target = self.write()

        response = cmdshellmcp2.applyPatch("example.txt", self.diff())

        self.assertFalse(response.startswith("Error:"), response)
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
        self.assertIn("patching file example.txt", response)

    def test_default_pnum_is_two(self):
        self.write()
        completed = mock.Mock(returncode=0, stdout="done", stderr="")
        with mock.patch.object(cmdshellmcp2.subprocess, "run", return_value=completed) as run:
            cmdshellmcp2.applyPatch("example.txt", self.diff())
        self.assertEqual(run.call_args.args[0][:3], ["patch", "--verbose", "-p2"])
        self.assertEqual(run.call_args.kwargs["input"], self.diff())

    def test_different_valid_pnum(self):
        for pnum in (0, 1):
            with self.subTest(pnum=pnum):
                target = self.write()
                response = cmdshellmcp2.applyPatch("example.txt", self.diff(), pnum=pnum)
                self.assertFalse(response.startswith("Error:"), response)
                self.assertEqual(target.read_text(), "new\n")

    def test_invalid_pnum(self):
        self.write()
        for pnum in (-1, True, False, 1.5, "1"):
            with self.subTest(pnum=pnum):
                self.assertTrue(
                    cmdshellmcp2.applyPatch("example.txt", self.diff(), pnum=pnum).startswith("Error:")
                )

    def test_rejects_unsafe_or_invalid_targets(self):
        self.write("directory/inside.txt")
        (cmdshellmcp2.cwd / "not-a-file").mkdir()
        targets = ("/tmp/example.txt", "../example.txt", "missing.txt", "not-a-file")
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue(cmdshellmcp2.applyPatch(target, self.diff()).startswith("Error:"))

    def test_none_args_is_accepted(self):
        self.write()
        response = cmdshellmcp2.applyPatch("example.txt", self.diff(), args=None)
        self.assertFalse(response.startswith("Error:"), response)

    def test_malformed_args_are_rejected(self):
        self.write()
        for args in ("--force", ["--force", 1], {"--force"}, [None]):
            with self.subTest(args=args):
                response = cmdshellmcp2.applyPatch("example.txt", self.diff(), args=args)
                self.assertTrue(response.startswith("Error:"))

    def test_file_selecting_and_strip_args_are_rejected(self):
        self.write()
        unsafe_args = (
            ["-o", "outfile"],
            ["--output=outfile"],
            ["-i", "patchfile"],
            ["--directory=elsewhere"],
            ["-p0"],
            ["--strip=0"],
        )
        for args in unsafe_args:
            with self.subTest(args=args):
                response = cmdshellmcp2.applyPatch("example.txt", self.diff(), args=args)
                self.assertTrue(response.startswith("Error:"))

    def test_filename_beginning_with_dash_is_not_an_option(self):
        target = self.write("-example.txt")
        response = cmdshellmcp2.applyPatch("-example.txt", self.diff(), pnum=0)
        self.assertFalse(response.startswith("Error:"), response)
        self.assertEqual(target.read_text(), "new\n")

    def test_failed_patch_returns_error(self):
        target = self.write()
        response = cmdshellmcp2.applyPatch("example.txt", self.diff().replace("-old", "-wrong"))
        self.assertTrue(response.startswith("Error:"), response)
        self.assertEqual(target.read_text(), "old\n")


if __name__ == "__main__":
    unittest.main()
