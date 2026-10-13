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


if __name__ == "__main__":
    unittest.main()
