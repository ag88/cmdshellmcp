from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from fastmcp import Client

import cmdshellClient
import cmdshellmcp2


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote"
        self.remote.mkdir()
        self.output = self.root / "download.zip"
        cwd = mock.patch.object(cmdshellmcp2, "cwd", self.remote)
        cwd.start()
        self.addCleanup(cwd.stop)
        self.server = cmdshellmcp2.create_server(["date"], "")
        factory = mock.patch.object(cmdshellClient, "Client", side_effect=lambda *a, **kw: Client(self.server))
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    async def download(self, overwrite=False):
        return await cmdshellClient.download_zip(self.output, "http://localhost:8080/mcp", "token", overwrite)

    async def test_downloads_nested_unicode_and_empty_files_and_directories(self):
        (self.remote / "src").mkdir()
        (self.remote / "empty-dir").mkdir()
        (self.remote / "src" / "hello.txt").write_text("héllo 世界\n", encoding="utf-8")
        (self.remote / "empty.txt").touch()
        self.assertEqual(await self.download(), 2)
        with ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {"src/", "src/hello.txt", "empty-dir/", "empty.txt"})
            self.assertEqual(archive.read("src/hello.txt"), "héllo 世界\n".encode())
            self.assertEqual(archive.read("empty.txt"), b"")
            self.assertIsNone(archive.testzip())
        self.factory.assert_called_once_with("http://localhost:8080/mcp", auth="token")

    async def test_empty_remote_directory_produces_valid_zip(self):
        self.assertEqual(await self.download(), 0)
        with ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), [])

    async def test_existing_archive_requires_overwrite(self):
        self.output.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            await self.download()
        self.assertEqual(self.output.read_bytes(), b"original")
        self.factory.assert_not_called()
        await self.download(overwrite=True)
        with ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), [])

    async def test_binary_read_failure_preserves_existing_archive_and_cleans_temp(self):
        (self.remote / "binary.bin").write_bytes(b"\xff\x00")
        self.output.write_bytes(b"original")
        with self.assertRaisesRegex(RuntimeError, "Error reading file"):
            await self.download(overwrite=True)
        self.assertEqual(self.output.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob(".dlzip-*")), [])

    async def test_failed_listing_does_not_publish_archive(self):
        self.server = cmdshellmcp2.create_server(["date"], "", disable_tools=["listFiles"])
        with self.assertRaises(Exception):
            await self.download()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".dlzip-*")), [])

    async def test_rejects_archive_traversal_from_server(self):
        self.server = cmdshellmcp2.create_server(["date"], "", disable_tools=["listFiles"])

        @self.server.tool(name="listFiles")
        def unsafe_listing(path: str = ".") -> str:
            return "../outside.txt"

        with self.assertRaisesRegex(RuntimeError, "Unsafe directory entry"):
            await self.download()
        self.assertFalse(self.output.exists())

    async def test_file_created_during_download_is_not_overwritten(self):
        self.server = cmdshellmcp2.create_server(["date"], "", disable_tools=["listFiles"])

        @self.server.tool(name="listFiles")
        def concurrent_creation(path: str = ".") -> str:
            self.output.write_bytes(b"concurrent")
            return ""

        with self.assertRaises(FileExistsError):
            await self.download()
        self.assertEqual(self.output.read_bytes(), b"concurrent")
        self.assertEqual(list(self.root.glob(".dlzip-*")), [])


class CommandLineTests(unittest.TestCase):
    def test_cmdshell_help_order_and_quoted_command_requirement(self):
        help_text = cmdshellClient.build_parser().format_help()
        self.assertIn("dlzip,cmdshell,listFiles", help_text)
        with redirect_stdout(io.StringIO()) as stdout, self.assertRaises(SystemExit) as raised:
            cmdshellClient.build_parser().parse_args(["cmdshell", "-h"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("ONE quoted string after URL", " ".join(stdout.getvalue().split()))
        self.assertIn("--auth", stdout.getvalue())

    def test_cmdshell_rejects_missing_empty_malformed_or_unquoted_commands(self):
        for arguments in ([], ["http://localhost/mcp"], ["http://localhost/mcp", ""],
                          ["http://localhost/mcp", "   "], ["http://localhost/mcp", "''"],
                          ["http://localhost/mcp", "echo 'unterminated"],
                          ["http://localhost/mcp", "ls", "-la"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cmdshellClient.build_parser().parse_args(["cmdshell", *arguments])
            self.assertEqual(raised.exception.code, 2)

    def test_cmdshell_auth_argument_environment_and_prompt(self):
        for explicit, environment, prompted, expected in (
            ("provided", {"CSMCP_AUTH_TOKEN": "env-token"}, None, "provided"),
            ("", {"CSMCP_AUTH_TOKEN": "env-token"}, None, ""),
            (None, {"CSMCP_AUTH_TOKEN": "env-token"}, None, "env-token"),
            (None, {}, "prompt-token", "prompt-token"),
            (None, {}, "", ""),
        ):
            with self.subTest(explicit=explicit, environment=environment, prompted=prompted):
                arguments = ["cmdshell", "http://localhost/mcp", "ls -la"]
                if explicit is not None:
                    arguments += ["--auth", explicit]
                with mock.patch.dict(cmdshellClient.os.environ, environment, clear=True), mock.patch.object(cmdshellClient.getpass, "getpass", return_value=prompted) as prompt, mock.patch.object(cmdshellClient, "run_cmdshell", new_callable=mock.AsyncMock) as run, redirect_stderr(io.StringIO()):
                    self.assertEqual(cmdshellClient.main(arguments), 0)
                self.assertEqual(run.await_args.args[0].remote_command, ["ls", "-la"])
                self.assertEqual(run.await_args.args[1], expected)
                self.assertEqual(prompt.call_count, int(prompted is not None))

    def test_parser_requires_command_filename_and_url(self):
        for args in ([], ["other"], ["dlzip"], ["dlzip", "file.zip"], ["dlzip", "file.txt", "http://localhost/mcp"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cmdshellClient.build_parser().parse_args(args)
            self.assertEqual(raised.exception.code, 2)

    def test_prompt_and_explicit_auth(self):
        for supplied, prompted, expected in ((None, "secret", "secret"), (None, "", ""), ("provided", None, "provided"), ("", None, "")):
            with self.subTest(supplied=supplied, prompted=prompted):
                args = ["dlzip", "file.zip", "http://localhost/mcp", "--overwrite"]
                if supplied is not None:
                    args += ["--auth", supplied]
                with mock.patch.object(cmdshellClient.getpass, "getpass", return_value=prompted) as prompt, mock.patch.object(cmdshellClient, "download_zip", new_callable=mock.AsyncMock, return_value=3) as download, redirect_stdout(io.StringIO()):
                    self.assertEqual(cmdshellClient.main(args), 0)
                download.assert_awaited_once_with(Path("file.zip"), "http://localhost/mcp", expected, True)
                self.assertEqual(prompt.call_count, int(supplied is None))

    def test_download_error_returns_nonzero(self):
        with mock.patch.object(cmdshellClient, "download_zip", new_callable=mock.AsyncMock, side_effect=RuntimeError("read failed")), redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(cmdshellClient.main(["dlzip", "file.zip", "http://localhost/mcp", "--auth", ""]), 1)
        self.assertIn("read failed", stderr.getvalue())


class FileToolTests(unittest.TestCase):
    url = "http://localhost:8003/mcp"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote"
        self.remote.mkdir()
        cwd = mock.patch.object(cmdshellmcp2, "cwd", self.remote)
        cwd.start()
        self.addCleanup(cwd.stop)
        audit = mock.patch.object(cmdshellmcp2, "audit")
        audit.start()
        self.addCleanup(audit.stop)
        self.server = cmdshellmcp2.create_server(["date"], "")
        factory = mock.patch.object(cmdshellClient, "Client", side_effect=lambda *a, **kw: Client(self.server))
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    def run_command(self, command, *arguments, stdin=""):
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr, mock.patch.object(cmdshellClient.sys, "stdin", io.StringIO(stdin)):
            status = cmdshellClient.main([command, self.url, *arguments, "--auth", "token"])
        self.factory.assert_called_with(self.url, auth="token")
        return status, stdout.getvalue(), stderr.getvalue()

    def test_list_default_and_explicit_directory(self):
        (self.remote / "src").mkdir()
        (self.remote / "src" / "a.txt").touch()
        self.assertEqual(self.run_command("listFiles"), (0, "src/\n", ""))
        self.assertEqual(self.run_command("listFiles", "src"), (0, "a.txt\n", ""))

    def test_cmdshell_tokenizes_and_preserves_quoted_and_empty_arguments(self):
        with mock.patch.object(cmdshellmcp2, "ALLOWED_COMMANDS", ["echo"]), mock.patch.object(
            cmdshellmcp2.subprocess, "run", wraps=cmdshellmcp2.subprocess.run
        ) as execute:
            self.assertEqual(self.run_command("cmdshell", "echo -n 'hello world' '' tail"),
                             (0, "hello world  tail", ""))
            self.assertEqual(execute.call_args.args[0], ["echo", "-n", "hello world", "", "tail"])
            self.assertEqual(self.run_command("cmdshell", "echo"), (0, "\n", ""))
            self.assertEqual(execute.call_args.args[0], ["echo"])

    def test_cmdshell_denial_and_path_errors_return_nonzero(self):
        with mock.patch.object(cmdshellmcp2, "ALLOWED_COMMANDS", ["echo"]):
            for command, error in (("disallowed-command", "Access Denied:"), ("echo ../outside", "Error:")):
                with self.subTest(command=command):
                    status, stdout, stderr = self.run_command("cmdshell", command)
                    self.assertEqual(status, 1)
                    self.assertEqual(stdout, "")
                    self.assertIn(error, stderr)

    def test_read_stdout_and_local_output_preserve_text(self):
        text = "héllo 世界\nlast line"
        (self.remote / "a.txt").write_text(text, encoding="utf-8")
        self.assertEqual(self.run_command("readFile", "a.txt"), (0, text, ""))
        output = self.root / "local.txt"
        self.assertEqual(self.run_command("readFile", "a.txt", "--out", str(output)),
                         (0, f"Output written to {output}\n", ""))
        self.assertEqual(output.read_bytes(), text.encode("utf-8"))

    def test_existing_output_is_rejected_before_auth_or_network(self):
        output = self.root / "local.txt"
        output.write_text("keep me")
        with mock.patch.object(cmdshellClient.getpass, "getpass") as prompt, redirect_stderr(io.StringIO()) as stderr:
            status = cmdshellClient.main(["readFile", self.url, "a.txt", "--out", str(output)])
        self.assertEqual(status, 1)
        self.assertIn("already exists", stderr.getvalue())
        self.assertEqual(output.read_text(), "keep me")
        prompt.assert_not_called()
        self.factory.assert_not_called()

    def test_concurrently_created_output_is_preserved(self):
        output = self.root / "local.txt"
        self.server = cmdshellmcp2.create_server(["date"], "", disable_tools=["readFile"])

        @self.server.tool(name="readFile")
        def concurrent_creation(file: str) -> str:
            output.write_text("keep me")
            return "remote text"

        status, stdout, stderr = self.run_command("readFile", "a.txt", "--out", str(output))
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("failed", stderr)
        self.assertEqual(output.read_text(), "keep me")

    def test_write_stdin_replaces_and_flags_append_newline(self):
        target = self.remote / "a.txt"
        target.write_text("old text")
        self.assertEqual(self.run_command("writeFile", "a.txt", stdin="first")[0], 0)
        self.assertEqual(target.read_text(), "first")
        self.assertEqual(self.run_command("writeFile", "a.txt", "--append", "--newline", stdin=" second")[0], 0)
        self.assertEqual(target.read_text(), "first second\n")

    def test_write_local_file_does_not_read_stdin(self):
        source = self.root / "input.txt"
        source.write_text("héllo 世界", encoding="utf-8")
        status, stdout, stderr = self.run_command("writeFile", "a.txt", "--in", str(source), stdin="ignored")
        self.assertEqual(status, 0, stderr)
        self.assertIn("Success:", stdout)
        self.assertEqual((self.remote / "a.txt").read_text(encoding="utf-8"), "héllo 世界")

    def test_directory_copy_move_delete_lifecycle(self):
        (self.remote / "original.txt").write_text("content")
        for command, arguments in (
            ("mkdir", ["drafts"]),
            ("copyFile", ["original.txt", "drafts/copy.txt"]),
            ("moveRenameFile", ["drafts/copy.txt", "drafts/final.txt"]),
        ):
            status, stdout, stderr = self.run_command(command, *arguments)
            self.assertEqual(status, 0, stderr)
            self.assertIn("Success:", stdout)
        self.assertEqual((self.remote / "drafts/final.txt").read_text(), "content")
        self.assertFalse((self.remote / "drafts/copy.txt").exists())
        self.assertEqual(self.run_command("deleteFile", "drafts/final.txt")[0], 0)
        self.assertEqual(self.run_command("rmdir", "drafts")[0], 0)
        self.assertFalse((self.remote / "drafts").exists())
        self.assertTrue((self.remote / "original.txt").exists())

    def test_edit_requires_script_before_authentication(self):
        with mock.patch.object(cmdshellClient.getpass, "getpass") as prompt, redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as raised:
            cmdshellClient.main(["editFile", self.url, "a.txt"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("requires -e", stderr.getvalue())
        prompt.assert_not_called()
        self.factory.assert_not_called()

    def test_edit_expression_and_script_file_precedence(self):
        target = self.remote / "a.txt"
        target.write_text("old\n")
        with mock.patch.object(cmdshellClient, "call_text", wraps=cmdshellClient.call_text) as call:
            status, stdout, stderr = self.run_command("editFile", "a.txt", "-e", "s/old/new/")
        self.assertEqual(status, 0, stderr)
        self.assertIn("Success:", stdout)
        self.assertEqual(target.read_text(), "new\n")
        self.assertEqual(call.call_args.args[2], {"file": "a.txt", "script": "s/old/new/", "args": None})
        script_file = self.root / "edit.sed"
        script_file.write_text("s/(new)/updated/\n", encoding="utf-8")
        with mock.patch.object(cmdshellClient, "call_text", wraps=cmdshellClient.call_text) as call:
            status, stdout, stderr = self.run_command("editFile", "a.txt", "-e", "ignored invalid script", "-f", str(script_file), "--args=-E")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(target.read_text(), "updated\n")
        self.assertEqual(call.call_args.args[2], {"file": "a.txt", "script": "s/(new)/updated/\n", "args": ["-E"]})

    def test_apply_patch_default_and_explicit_options(self):
        target = self.remote / "a.txt"
        diff_file = self.root / "changes.diff"
        diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"
        diff_file.write_text(diff, encoding="utf-8")
        for options, pnum, tool_args in (([], 2, None), (["--pnum", "1", "--args=--batch --verbose"], 1, ["--batch", "--verbose"])):
            with self.subTest(options=options):
                target.write_text("old\n")
                with mock.patch.object(cmdshellClient, "call_text", wraps=cmdshellClient.call_text) as call:
                    status, stdout, stderr = self.run_command("applyPatch", "a.txt", str(diff_file), *options)
                self.assertEqual(status, 0, stderr)
                self.assertIn("Hunk #1 succeeded", stdout)
                self.assertEqual(target.read_text(), "new\n")
                self.assertEqual(call.call_args.args[2], {"file": "a.txt", "diff": diff, "pnum": pnum, "args": tool_args})

    def test_edit_and_patch_local_read_failures_do_not_connect(self):
        for command, arguments in (("editFile", ["a.txt", "-e", "s/old/new/", "-f", str(self.root / "missing.sed")]),
                                   ("applyPatch", ["a.txt", str(self.root / "missing.diff")])):
            with self.subTest(command=command), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(cmdshellClient.main([command, self.url, *arguments, "--auth", ""]), 1)
                self.assertIn("failed", stderr.getvalue())
        self.factory.assert_not_called()

    def test_edit_and_patch_server_rejections_and_invalid_args(self):
        target = self.remote / "a.txt"
        target.write_text("old\n")
        diff_file = self.root / "changes.diff"
        diff_file.write_text("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n")
        for command, arguments in (("editFile", ["a.txt", "-e", "s/old/new/", "--args=-i"]),
                                   ("applyPatch", ["a.txt", str(diff_file), "--args=--output=elsewhere"])):
            with self.subTest(command=command):
                status, stdout, stderr = self.run_command(command, *arguments)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn("not allowed", stderr)
                self.assertEqual(target.read_text(), "old\n")
        for arguments in (["applyPatch", self.url, "a.txt"],
                          ["applyPatch", self.url, "a.txt", "diff", "--pnum", "not-an-int"],
                          ["editFile", self.url, "a.txt", "-e", "s/old/new/", "--args=\"unterminated"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cmdshellClient.main(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_tool_errors_return_nonzero_and_do_not_create_output(self):
        output = self.root / "local.txt"
        for command, arguments in (
            ("readFile", ["missing.txt", "--out", str(output)]),
            ("listFiles", ["missing"]),
            ("writeFile", ["missing/a.txt"]),
            ("mkdir", ["missing/sub"]),
            ("rmdir", ["missing"]),
            ("deleteFile", ["missing.txt"]),
            ("copyFile", ["missing.txt", "other.txt"]),
            ("moveRenameFile", ["missing.txt", "other.txt"]),
        ):
            with self.subTest(command=command):
                status, stdout, stderr = self.run_command(command, *arguments)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn("failed", stderr)
                self.assertFalse(output.exists())

    def test_required_arguments_and_auth_prompt_for_every_command(self):
        for command, arguments in (
            ("listFiles", []), ("readFile", ["a.txt"]), ("writeFile", ["a.txt"]),
            ("editFile", ["a.txt", "-e", "s/old/new/"]), ("applyPatch", ["a.txt", "changes.diff"]),
            ("mkdir", ["dir"]), ("rmdir", ["dir"]), ("deleteFile", ["a.txt"]),
            ("copyFile", ["a.txt", "b.txt"]), ("moveRenameFile", ["a.txt", "b.txt"]),
        ):
            with self.subTest(command=command):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    cmdshellClient.build_parser().parse_args([command])
                if arguments:
                    with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        cmdshellClient.build_parser().parse_args([command, self.url, *arguments[:-1]])
                with mock.patch.object(cmdshellClient, "run_file_tool", new_callable=mock.AsyncMock) as run, mock.patch.object(cmdshellClient.getpass, "getpass", return_value="") as prompt, redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(cmdshellClient.main([command, self.url, *arguments]), 0)
                prompt.assert_called_once()
                self.assertEqual(run.await_args.args[1], "")
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("not displayed / echoed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
