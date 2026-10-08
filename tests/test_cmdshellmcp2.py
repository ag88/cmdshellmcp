import asyncio
import argparse
import json
import runpy
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cmdshellmcp2


class CmdshellTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.cwd_patch = mock.patch.object(cmdshellmcp2, "cwd", Path(temporary_directory.name))
        self.cwd_patch.start()
        self.addCleanup(self.cwd_patch.stop)
        for name, value in (("ALLOWED_COMMANDS", ["echo"]), ("SHELL_NO_PATH_CHECK", False)):
            patch = mock.patch.object(cmdshellmcp2, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(
            cmdshellmcp2.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="done", stderr=""),
        )
        self.run = patch.start()
        self.addCleanup(patch.stop)

    def test_rejects_absolute_and_parent_paths_before_execution(self):
        for argument in ("/tmp/file", "../file", "sub/../../file", "/tmp/*", "../*"):
            with self.subTest(argument=argument):
                with mock.patch.object(cmdshellmcp2.glob, "glob") as glob:
                    response = cmdshellmcp2.cmdshell("echo", [argument])
                self.assertTrue(response.startswith("Error:"), response)
                glob.assert_not_called()
                self.run.assert_not_called()

    def test_checks_arguments_after_decoding_and_unquoting(self):
        for argument in ('"/tmp/file"', '"../file"', r"\x2ftmp/file", r"\x2e\x2e/file", "", '""'):
            with self.subTest(argument=argument):
                response = cmdshellmcp2.cmdshell("echo", [argument])
                self.assertTrue(response.startswith("Error:"), response)
                self.run.assert_not_called()

    def test_accepts_flags_text_and_local_paths(self):
        args = ["-n", "hello", "sub/file", ".", "file..txt"]
        self.assertEqual(cmdshellmcp2.cmdshell("echo", args), "done")
        self.assertEqual(self.run.call_args.args[0], ["echo"] + args)

    def test_accepts_omitted_arguments(self):
        self.assertEqual(cmdshellmcp2.cmdshell("echo"), "done")
        self.assertEqual(self.run.call_args.args[0], ["echo"])

    def test_expands_globs_in_configured_working_directory(self):
        (cmdshellmcp2.cwd / "example.txt").write_text("example")
        self.assertEqual(cmdshellmcp2.cmdshell("echo", ["*.txt"]), "done")
        self.assertEqual(self.run.call_args.args[0], ["echo", "example.txt"])
        self.assertEqual(self.run.call_args.kwargs["cwd"], cmdshellmcp2.cwd)

    def test_quoted_and_unmatched_globs_are_preserved(self):
        (cmdshellmcp2.cwd / "example.txt").write_text("example")
        self.assertEqual(cmdshellmcp2.cmdshell("echo", ['"*.txt"', "*.missing"]), "done")
        self.assertEqual(self.run.call_args.args[0], ["echo", "*.txt", "*.missing"])

    def test_rejects_unsafe_expanded_matches(self):
        with mock.patch.object(cmdshellmcp2.glob, "glob", return_value=["../outside"]):
            response = cmdshellmcp2.cmdshell("echo", ["*"])
        self.assertTrue(response.startswith("Error:"), response)
        self.run.assert_not_called()

    def test_bypass_allows_paths_and_empty_arguments(self):
        cmdshellmcp2.SHELL_NO_PATH_CHECK = True
        self.assertEqual(cmdshellmcp2.cmdshell("echo", ['"/tmp/file"', "../file", ""]), "done")
        self.assertEqual(self.run.call_args.args[0], ["echo", "/tmp/file", "../file", ""])

    def test_bypass_keeps_allowlist_and_file_tool_checks(self):
        cmdshellmcp2.SHELL_NO_PATH_CHECK = True
        self.assertTrue(cmdshellmcp2.cmdshell("cat", ["/tmp/file"]).startswith("Access Denied:"))
        self.assertTrue(cmdshellmcp2.writeFile("../file", "example").startswith("Error:"))
        self.run.assert_not_called()

    def test_description_reports_effective_path_policy(self):
        self.assertIn("Arguments are checked", cmdshellmcp2.cmdshell_description(["echo"]))
        cmdshellmcp2.SHELL_NO_PATH_CHECK = True
        self.assertIn("disabled by --shellnopathchk", cmdshellmcp2.cmdshell_description(["echo"]))


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


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.parser = argparse.ArgumentParser()
        self.default_search_paths = cmdshellmcp2.CONFIG_SEARCH_PATHS
        self.paths = tuple(self.root / name for name in ("etc.json", "install.json"))
        patch = mock.patch.object(cmdshellmcp2, "CONFIG_SEARCH_PATHS", self.paths)
        patch.start()
        self.addCleanup(patch.stop)

    def test_config_search_uses_first_existing_file_without_merging(self):
        with self.assertRaises(SystemExit):
            cmdshellmcp2.load_config(self.parser, None)
        for index in reversed(range(len(self.paths))):
            self.paths[index].write_text(json.dumps({"port": 8000 + index}))
            self.assertEqual(cmdshellmcp2.load_config(self.parser, None), {"port": 8000 + index})

    def test_explicit_config_bypasses_search_even_when_missing(self):
        self.paths[0].write_text('{"port": 9000}')
        explicit = self.root / "explicit.json"
        with self.assertRaises(SystemExit):
            cmdshellmcp2.load_config(self.parser, str(explicit))
        explicit.write_text('{"port": 9001}')
        self.assertEqual(cmdshellmcp2.load_config(self.parser, str(explicit)), {"port": 9001})

    def test_invalid_selected_config_does_not_fall_through(self):
        self.paths[1].write_text('{"port": 9000}')
        for content in ("invalid json", "[]"):
            self.paths[0].write_text(content)
            with self.assertRaises(SystemExit):
                cmdshellmcp2.load_config(self.parser, None)

    def test_cwd_cli_overrides_config_and_config_skips_prompt(self):
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(cmdshellmcp2.resolve_working_directory(
            self.parser, str(other), {"cwd": str(self.root)}), other)
        self.assertEqual(cmdshellmcp2.resolve_working_directory(
            self.parser, None, {"cwd": str(self.root)}), self.root)

    def test_missing_cwd_fails_without_prompt_or_process_directory_fallback(self):
        with mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")), mock.patch.object(
            cmdshellmcp2.Path, "cwd", side_effect=AssertionError("unexpected cwd fallback")
        ):
            for config in ({}, {"cwd": None}):
                with self.subTest(config=config), self.assertRaises(SystemExit):
                    cmdshellmcp2.resolve_working_directory(self.parser, None, config)

    def test_current_directory_config_is_not_discovered(self):
        import os
        original = Path.cwd()
        self.addCleanup(os.chdir, original)
        os.chdir(self.root)
        Path("cmdshellmcp.json").write_text('{}')
        self.assertEqual(self.default_search_paths, (
            Path("/etc/cmdshellmcp.d/cmdshellmcp.json"),
            Path("/usr/local/python/cmdshellmcp/cmdshellmcp.json"),
        ))
        with self.assertRaises(SystemExit):
            cmdshellmcp2.load_config(self.parser, None)

    def test_config_inside_working_directory_logs_warning(self):
        config = self.root / "sub" / "config.json"
        config.parent.mkdir()
        config.write_text('{}')
        with self.assertLogs(cmdshellmcp2.log, level="WARNING") as logs:
            cmdshellmcp2.warn_config_in_working_directory(config, self.root)
        self.assertIn(str(config), logs.output[0])
        self.assertIn(str(self.root), logs.output[0])
        self.assertIn("client may read or modify", logs.output[0])

    def test_config_outside_working_directory_does_not_warn(self):
        workdir = self.root / "work"
        workdir.mkdir()
        config = self.root / "work-other" / "config.json"
        config.parent.mkdir()
        config.write_text('{}')
        with mock.patch.object(cmdshellmcp2.log, "warning") as warning:
            cmdshellmcp2.warn_config_in_working_directory(config, workdir)
        warning.assert_not_called()

    def test_config_symlink_exposure_logs_warning(self):
        inside = self.root / "inside"
        outside = self.root / "outside"
        inside.mkdir()
        outside.mkdir()
        target = inside / "config.json"
        target.write_text('{}')
        link = outside / "config.json"
        link.symlink_to(target)
        with self.assertLogs(cmdshellmcp2.log, level="WARNING"):
            cmdshellmcp2.warn_config_in_working_directory(link, inside)
        with self.assertLogs(cmdshellmcp2.log, level="WARNING"):
            cmdshellmcp2.warn_config_in_working_directory(link, outside)

    def test_invalid_config_cwd_fails_without_prompt(self):
        for value in (False, 42, "", "  ", str(self.root / "missing")):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                cmdshellmcp2.resolve_working_directory(
                    self.parser, None, {"cwd": value})
        file = self.root / "file"
        file.touch()
        with self.assertRaises(SystemExit):
            cmdshellmcp2.resolve_working_directory(self.parser, str(file))

    def test_startup_missing_config_or_cwd_fails_before_registration(self):
        config = self.root / "empty.json"
        config.write_text('{}')
        for flags, message in (
            (["--conf", str(self.root / "missing.json"), "--cwd", str(self.root)], "config file not found"),
            (["--conf", str(config)], "missing current working directory"),
            (["--conf", str(config), "--cwd", ""], "missing current working directory"),
        ):
            with mock.patch.object(sys, "argv", ["cmdshellmcp2.py", *flags]), mock.patch(
                "fastmcp.server.FastMCP"
            ) as server, mock.patch("sys.stderr") as stderr:
                with self.assertRaises(SystemExit) as exc:
                    runpy.run_path(str(Path(cmdshellmcp2.__file__)), run_name="__main__")
                self.assertEqual(exc.exception.code, 2)
                self.assertIn(message, "".join(call.args[0] for call in stderr.write.call_args_list))
                server.assert_not_called()

    def test_new_boolean_keys_require_json_booleans(self):
        for key in ("shellnopathchk", "editdelbk"):
            self.assertFalse(cmdshellmcp2.config_value(self.parser, {}, key, False))
            for value in (True, False):
                self.assertIs(cmdshellmcp2.config_value(self.parser, {key: value}, key, False), value)
            for value in ("false", 0, 1, None, []):
                with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                    cmdshellmcp2.config_value(self.parser, {key: value}, key, False)

    def test_startup_resolves_boolean_config_and_cli_before_registration(self):
        config = self.root / "startup.json"
        for configured, flags, expected in (
            ({}, [], False),
            ({"shellnopathchk": True, "editdelbk": True}, [], True),
            ({"shellnopathchk": False, "editdelbk": False}, ["--shellnopathchk", "--editdelbk"], True),
        ):
            config.write_text(json.dumps({"cwd": str(self.root), **configured}))
            argv = ["cmdshellmcp2.py", "--conf", str(config), "--quiet", *flags]
            with mock.patch.object(sys, "argv", argv), mock.patch("fastmcp.server.FastMCP") as server:
                server.return_value.list_tools = mock.AsyncMock(return_value=[])
                with self.assertLogs("__main__", level="INFO") as logs:
                    state = runpy.run_path(str(Path(cmdshellmcp2.__file__)), run_name="__main__")
                self.assertIn("working directory:", logs.output[0])
                self.assertIn("config file", logs.output[1])
                self.assertIn("client may read or modify", logs.output[1])
            self.assertIs(state["SHELL_NO_PATH_CHECK"], expected)
            self.assertIs(state["EDIT_DELETE_BACKUP"], expected)
            self.assertEqual(state["cwd"], self.root)


if __name__ == "__main__":
    unittest.main()
