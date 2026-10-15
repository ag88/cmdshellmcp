#!/usr/bin/env python3
"""Access cmdshellmcp file tools and download text files into a local ZIP archive."""

import argparse
import asyncio
import getpass
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from fastmcp import Client


def zip_filename(value: str) -> Path:
    path = Path(value).expanduser()
    if path.suffix.lower() != ".zip":
        raise argparse.ArgumentTypeError("filename must end in .zip")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
        epilog=f"""
            for specific command help,
            run with {sys.argv[0]} command -h , 
            e.g. {sys.argv[0]} dlzip -h
            """)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--auth", metavar="auth_token",
        help="bearer token, you can also set env var CSMCP_AUTH_TOKEN for the auth token, otherwise prompt if omitted")
    dlzip = commands.add_parser("dlzip", parents=[common], help="download zip", description="download zip")
    dlzip.add_argument("filename", type=zip_filename, metavar="filename.zip")
    dlzip.add_argument("url", help="MCP server URL, e.g. http://127.0.0.1:8003/mcp")
    dlzip.add_argument("--overwrite", action="store_true", help="overwrite filename.zip")
    descriptions = {
        "listFiles": "list remote directory entries",
        "readFile": "read a remote UTF-8 text file",
        "writeFile": "write text to a remote file",
        "mkdir": "create a remote directory",
        "rmdir": "remove an empty remote directory",
        "deleteFile": "delete a remote file",
        "copyFile": "copy a remote file",
        "moveRenameFile": "move or rename a remote file",
    }
    for name, description in descriptions.items():
        command = commands.add_parser(name, parents=[common], help=description, description=description)
        command.add_argument("url", help="MCP server URL, e.g. http://127.0.0.1:8003/mcp")
        if name == "listFiles":
            command.add_argument("path", metavar="dir", nargs="?", default=".")
        elif name in ("mkdir", "rmdir"):
            command.add_argument("path", metavar="dir")
        elif name in ("copyFile", "moveRenameFile"):
            command.add_argument("source", help="remote source file")
            command.add_argument("destination", help="remote destination file")
        else:
            command.add_argument("file", help="remote file")
        if name == "readFile":
            command.add_argument("--out", type=lambda value: Path(value).expanduser(), metavar="filename",
                                 help="save locally instead of stdout; must not already exist")
        elif name == "writeFile":
            command.add_argument("--in", dest="input_file", type=lambda value: Path(value).expanduser(),
                                 metavar="filename", help="read local UTF-8 text instead of stdin")
            command.add_argument("--append", action="store_true", help="append to the remote file")
            command.add_argument("--newline", action="store_true", help="add a trailing newline")
    return parser


async def call_text(client: Client, tool: str, arguments: dict) -> str:
    result = await client.call_tool(tool, arguments)
    if result.is_error:
        raise RuntimeError(f"{tool} failed")
    data = getattr(result, "data", None)
    if isinstance(data, str):
        return data
    if len(result.content) == 1 and result.content[0].type == "text":
        return result.content[0].text
    raise RuntimeError(f"{tool} returned an unexpected response; expected text")


async def write_directory(client: Client, archive: ZipFile, directory: str = ".") -> int:
    listing = await call_text(client, "listFiles", {"path": directory})
    if listing.startswith(("Error:", "Error listing files ")):
        raise RuntimeError(listing)
    count = 0
    for entry in listing.splitlines():
        is_directory = entry.endswith("/")
        name = entry[:-1] if is_directory else entry
        # listFiles returns single basenames. Never accept archive traversal paths.
        if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
            raise RuntimeError(f"Unsafe directory entry: {entry!r}")
        path = str(PurePosixPath(directory) / name)
        if is_directory:
            archive.writestr(path + "/", b"")
            count += await write_directory(client, archive, path)
        else:
            text = await call_text(client, "readFile", {"file": path})
            if text.startswith(("Error:", f"Error reading file {path}:")):
                raise RuntimeError(text)
            archive.writestr(path, text.encode("utf-8"))
            count += 1
    return count


async def download_zip(filename: Path, url: str, auth: str | None, overwrite: bool = False) -> int:
    """Build beside the destination, publishing only a complete archive."""
    if not overwrite and os.path.lexists(filename):
        raise FileExistsError(f"{filename} already exists; use --overwrite to replace it")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=filename.parent, prefix=".dlzip-", suffix=".zip", delete=False) as output:
            temporary = Path(output.name)
        async with Client(url, auth=auth or None) as client:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                count = await write_directory(client, archive)
        if overwrite:
            os.replace(temporary, filename)
        else:
            # A hard link publishes atomically without replacing a file created
            # while the download was running. Both paths are on the same volume.
            os.link(temporary, filename)
        return count
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def run_file_tool(args: argparse.Namespace, token: str | None) -> None:
    if args.command in ("listFiles", "mkdir", "rmdir"):
        arguments = {"path": args.path}
    elif args.command in ("copyFile", "moveRenameFile"):
        arguments = {"source": args.source, "destination": args.destination}
    else:
        arguments = {"file": args.file}
    if args.command == "writeFile":
        text = args.input_file.read_text(encoding="utf-8") if args.input_file else sys.stdin.read()
        arguments.update(text=text, append=args.append, newline=args.newline)
    async with Client(args.url, auth=token or None) as client:
        text = await call_text(client, args.command, arguments)
    if args.command == "readFile":
        error_prefixes = ("Error:", f"Error reading file {args.file}:")
    elif args.command == "listFiles":
        error_prefixes = ("Error:", "Error listing files ")
    else:
        error_prefixes = ("Error:", "Error ")
    if text.startswith(error_prefixes):
        raise RuntimeError(text)
    if args.command == "readFile":
        if args.out is None:
            sys.stdout.write(text)
        else:
            # Exclusive creation also protects a file created during the MCP call.
            with args.out.open("xb") as output:
                output.write(text.encode("utf-8"))
            print(f"Output written to {args.out}")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dlzip" and not args.overwrite and os.path.lexists(args.filename):
            raise FileExistsError(f"{args.filename} already exists; use --overwrite to replace it")
        if args.command == "readFile" and args.out is not None and os.path.lexists(args.out):
            raise FileExistsError(f"{args.out} already exists")
        token = args.auth
        if token is None:
            if "CSMCP_AUTH_TOKEN" in os.environ:
                token = os.environ["CSMCP_AUTH_TOKEN"]
            else:
                print("note: auth token input is not displayed / echoed", file=sys.stderr)
                token = getpass.getpass("Auth token (Enter for no authentication): ")

        if args.command == "dlzip":
            count = asyncio.run(download_zip(args.filename, args.url, token, args.overwrite))
            print(f"Downloaded {count} file(s) to {args.filename}")
        else:
            asyncio.run(run_file_tool(args, token))
    except (KeyboardInterrupt, EOFError):
        print("Download cancelled." if args.command == "dlzip" else "Command cancelled.", file=sys.stderr)
        return 1
    except Exception as exc:
        label = "Download" if args.command == "dlzip" else args.command
        print(f"{label} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
