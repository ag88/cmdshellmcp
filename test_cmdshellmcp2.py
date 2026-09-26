import asyncio
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
