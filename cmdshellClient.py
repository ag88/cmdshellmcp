#!/usr/bin/env python3
"""Download a cmdshellmcp server's text files into a local ZIP archive."""

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
    dlzip = commands.add_parser("dlzip", help="download zip", description="download zip")
    dlzip.add_argument("filename", type=zip_filename, metavar="filename.zip")
    dlzip.add_argument("url", help="MCP server URL, e.g. http://127.0.0.1:8080/mcp")
    dlzip.add_argument("--overwrite", action="store_true", help="overwrite filename.zip")
    dlzip.add_argument("--auth", metavar="auth_token", help="bearer token (prompt if omitted)")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.overwrite and os.path.lexists(args.filename):
            raise FileExistsError(f"{args.filename} already exists; use --overwrite to replace it")
        token = args.auth
        if token is None:
            print("note: auth token input is not displayed / echoed")
            token = getpass.getpass("Auth token (Enter for no authentication): ")
        count = asyncio.run(download_zip(args.filename, args.url, token, args.overwrite))
    except (KeyboardInterrupt, EOFError):
        print("Download cancelled.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    print(f"Downloaded {count} file(s) to {args.filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
