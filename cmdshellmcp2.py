#!/usr/bin/env python3
from fastmcp.server import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
import asyncio
from pathlib import Path
import subprocess
import argparse
import shlex
import json
import glob
import requests
from bs4 import BeautifulSoup
import logging
import sys
from typing import Any, Optional

log = logging.getLogger(__name__)
audit = logging.getLogger("audit")
audit.propagate = False

cwd = Path.home()

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8003

CORS_MIDDLEWARE = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "mcp-protocol-version",
            "mcp-session-id",
            "Authorization",
            "Content-Type",
        ],
        expose_headers=["mcp-session-id"],
    )
]


def configure_audit(quiet: bool = False, logfile: Optional[str] = None) -> None:
    """Configure the session audit trail on stdout and, optionally, in a file."""
    audit.handlers.clear()
    audit.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    if not quiet:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        audit.addHandler(stdout_handler)

    if logfile:
        file_handler = logging.FileHandler(Path(logfile).expanduser(), encoding="utf-8")
        file_handler.setFormatter(formatter)
        audit.addHandler(file_handler)


configure_audit()

# Define your allowed whitelist
DEFAULT_ALLOWED_COMMANDS = ["ls", "pwd", "echo", "date", "whoami"]
# more
MORE_COMMANDS = ["cat", "head", "tail", "grep", "egrep", "sed", "wc", "file", "tree",
    "du", "df", "free", "ps", "uname", "hostname", "uptime", "w", "last"]
# file change commands
UPD_COMMANDS = ["mkdir", "cp", "mv", "awk"] 

DEFAULT_ALLOWED_COMMANDS.extend(MORE_COMMANDS)
DEFAULT_ALLOWED_COMMANDS.extend(UPD_COMMANDS)

##DEFAULT_ALLOWED_COMMANDS = [
##    "ls", "pwd", "echo", "date", "whoami",
##    "cat", "head", "tail", "grep", "egrep", "sed", "wc", "file", "du",
##    "df", "free", "ps", "uname", "hostname", "uptime", "w", "last",
##    "mkdir", "cp", "mv", "awk",
##]

# Replaced with the effective configuration before tools are registered.
ALLOWED_COMMANDS = DEFAULT_ALLOWED_COMMANDS.copy()


def cmdshell(command: str, args: Optional[list[str]] = None) -> str:
    command = command.strip()
    if command.find(" ") > 0:
        return "Error: separate and place each option and argument in the args list/array"

    if command not in ALLOWED_COMMANDS:
        return f"Access Denied: '{command}' is not in the allowed list."

    if args is None:
        args = []

    audit.info("command: %s %s", command, " ".join(args))

    # Resolve shell glob patterns in arguments
    expanded_args = []
    for arg in args:
        # Check for common glob characters
        arg = arg.encode().decode('unicode_escape')
        if arg.startswith('"') and arg.endswith('"'):
            arg = arg[1:-1] 
            expanded_args.append(arg)
        else: 
            if any(char in arg for char in ('*', '?', '[', ']')):
                try:
                    matches = glob.glob(arg)
                    if matches:
                        # Expand to all matching files/directories
                        expanded_args.extend(matches)
                    else:
                        # Keep original pattern if no matches (mimics standard shell behavior)
                        expanded_args.append(arg)
                except Exception:
                    # Fallback to original argument on glob resolution error
                    expanded_args.append(arg)
            else:
                expanded_args.append(arg)

    try:
        # Construct command safely as a list to prevent shell injection
        full_cmd = [command] + expanded_args
        log.debug("full cmd: %s", shlex.join(full_cmd))
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=100  # Prevents hanging processes
        )

        if result.returncode != 0:
            audit.error("command failed (%s): %s", result.returncode, result.stderr.rstrip())
            return result.stderr
        return result.stdout
    except subprocess.TimeoutExpired:
        audit.error("command timed out: %s", command)
        return "Error: Command execution timed out."
    except Exception as e:
        audit.exception("command execution failed: %s", command)
        return f"Error executing command: {str(e)}"


def _local_path_error(file: str) -> Optional[str]:
    """Return an error when a tool path could escape the configured directory."""
    if cwd is None:
        return "Error: the current directory is not set; report it to the user"
    if not file:
        return "Error: filename is required"
    if file.startswith("/") or Path(file).is_absolute():
        return "Error: absolute paths not allowed for writing"
    if ".." in Path(file).parts:
        return "Error: relative paths should be current directory and below"
    return None

def writeFile(file: str, text: str, append: bool = False, newline = True) -> str:
    """
    write text into a local file in the current directory
    
    Args:
        file: filename. only a local filename or dir/filename is allowed
        text: text to write
        append: append to the file, default false
        newline: terminate end of text with newline, default true
    Return:
        a string indicating status of write
    """
    error = _local_path_error(file)
    if error:
        audit.error("writeFile %r: %s", file, error)
        return error

    fpath = cwd / Path(file)

    audit.info("writeFile: %s (append=%s)", file, append)

    if append:
        op = "a"
    else:
        op = "w"

    try:
        with open(fpath, op) as f:
            f.write(text)
            if newline:
                f.write('\n')
            f.flush()
        return "Success: text written into {}".format(file)
    except Exception as e:
        audit.error("writeFile failed for %s: %s", file, e)
        return f"Error writing file {file}: {str(e)}"


def readFile(file: str) -> str:
    """Read and return a UTF-8 text file beneath the current directory."""
    error = _local_path_error(file)
    if error:
        audit.error("readFile %r: %s", file, error)
        return error
    audit.info("readFile: %s", file)
    try:
        return (cwd / file).read_text(encoding="utf-8")
    except Exception as exc:
        audit.error("readFile failed for %s: %s", file, exc)
        return f"Error reading file {file}: {exc}"


def listFiles(path: str = ".") -> str:
    """List entries in a directory beneath the current directory."""
    error = _local_path_error(path)
    if error:
        audit.error("listFiles %r: %s", path, error)
        return error
    audit.info("listFiles: %s", path)
    try:
        directory = cwd / path
        if not directory.is_dir():
            raise NotADirectoryError(path)
        entries = (entry.name + ("/" if entry.is_dir() else "") for entry in directory.iterdir())
        return "\n".join(sorted(entries))
    except Exception as exc:
        audit.error("listFiles failed for %s: %s", path, exc)
        return f"Error listing files {path}: {exc}"


def applyPatch(text: str, args: Optional[list[str]] = None, context: int = 2) -> str:
    """Apply context/unified diff text using patch; args are passed to patch.

    ``context`` sets patch's maximum fuzz (the number of context lines patch may
    ignore) and defaults to 2, matching diffs conventionally made with ``diff -C 2``.
    """
    if cwd is None:
        error = "Error: the current directory is not set; report it to the user"
        audit.error("applyPatch: %s", error)
        return error
    if not isinstance(context, int) or isinstance(context, bool) or context < 0:
        return "Error: context must be a non-negative integer"
    if args is None:
        args = []
    for line in text.splitlines():
        if line.startswith(("*** ", "--- ", "+++ ")):
            patch_name = line[4:].split("\t", 1)[0].strip()
            if patch_name in ("/dev/null", "dev/null"):
                continue
            # Context diff timestamps may be separated from the name by spaces.
            patch_name = patch_name.split("  ", 1)[0]
            if Path(patch_name).is_absolute():
                error = "Error: absolute paths not allowed for patching"
                audit.error("applyPatch: %s", error)
                return error
            if ".." in Path(patch_name).parts:
                error = "Error: relative paths should be current directory and below"
                audit.error("applyPatch: %s", error)
                return error
    # Options that select another directory or external input/output can escape cwd.
    unsafe = ("-d", "--directory", "-i", "--input", "-o", "--output", "-r", "--reject-file")
    has_unsafe_arg = any(
        arg in unsafe
        or any(arg.startswith(prefix + "=") for prefix in unsafe if prefix.startswith("--"))
        for arg in args
    )
    if has_unsafe_arg:
        error = "Error: patch path-changing and file input/output options are not allowed"
        audit.error("applyPatch: %s", error)
        return error

    command = ["patch", "--batch", "--fuzz", str(context), *args]
    audit.info("applyPatch: %s", shlex.join(command))
    try:
        result = subprocess.run(
            command, input=text, capture_output=True, text=True, cwd=cwd, timeout=100
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            audit.error("applyPatch failed (%s): %s", result.returncode, output.rstrip())
            return f"Error applying patch: {output.strip()}"
        return output or "Success: patch applied"
    except subprocess.TimeoutExpired:
        audit.error("applyPatch timed out")
        return "Error: Patch execution timed out."
    except Exception as exc:
        audit.exception("applyPatch execution failed")
        return f"Error applying patch: {exc}"


def fetch(url: str, prettify: bool = False) -> str:
    """
    fetch a page from a url
    Args:
        url: the url to fetch
        prettify: prettify the retrieved html
    """
    r = requests.get(url)
    if prettify:
        soup = BeautifulSoup(r.text, 'html.parser')
        return soup.prettify()
    else:
        return r.text


def cmdshell_description(allowed_commands: list[str]) -> str:
    """Build the description advertised to MCP clients for the shell tool."""
    allowed = json.dumps(allowed_commands)
    example_command = allowed_commands[0]
    return f"""Runs one of the configured Linux/Unix commands.

Allowed commands: {allowed}

Args:
    command: The command name (for example, {example_command!r}). It must be in the allowed commands list.
    args: A list of command arguments/flags (for example, ['-la', '*.txt']).
          Put a glob in double quotes (for example, ['\"*.txt\"']) to pass it
          literally instead of expanding it into filenames. Pipes are not allowed.
"""


def create_server(
    host: str,
    port: int,
    allowed_commands: list[str],
    auth: str,
    disable_tools: Optional[list[str]] = None,
) -> FastMCP:
    """Create the server and register tools after configuration is resolved."""
    disabled = set(disable_tools or [])
    if auth:
        verifier = StaticTokenVerifier(
            tokens={
                auth: {
                    "client_id": "guest-user",
                    "scopes": ["read:data", "write:data"]
                }
            },
            required_scopes=["read:data", "write:data"]
        )
    else:
        verifier = None
    server = FastMCP(
        name="LimitedShell",
        auth=verifier
    )
    tools = (
        ("cmdshell", cmdshell, cmdshell_description(allowed_commands)),
        ("writeFile", writeFile, None),
        ("readFile", readFile, None),
        ("listFiles", listFiles, None),
        ("applyPatch", applyPatch, None),
        ("fetch", fetch, None),
    )
    for name, tool, description in tools:
        if name not in disabled:
            server.tool(description=description)(tool)
    return server


async def listtools(mcp: FastMCP):
    tools = await mcp.list_tools()
    for t in tools:
        log.info(t.name)
        log.info(t.title)
        log.info(t.description)


class ArgumentParser(argparse.ArgumentParser):
    def convert_arg_line_to_args(self, line):
        line = line.strip()

        if not line or line.startswith('#'):
            return []

        return shlex.split(line)


def load_config(parser: argparse.ArgumentParser, filename: str) -> dict[str, Any]:
    """Read an optional JSON configuration file and validate its top-level shape."""
    config_path = Path(filename).expanduser()
    if not config_path.is_file():
        log.info("config file not found, using command-line/default values: %s", config_path)
        return {}

    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read config file {config_path}: {exc}")

    if not isinstance(config, dict):
        parser.error(f"config file {config_path} must contain a JSON object")
    return config


def normalize_commands(parser: argparse.ArgumentParser, value: Any, source: str) -> list[str]:
    """Validate, flatten and de-duplicate an allowlist while preserving order."""
    if not isinstance(value, list):
        parser.error(f"{source} allowed_commands must be a list of strings")

    commands = []
    for item in value:
        # argparse produces lists of lists; JSON produces a flat list.
        items = item if isinstance(item, list) else [item]
        for token in items:
            if not isinstance(token, str):
                parser.error(f"{source} allowed_commands must contain only strings")
            for command in token.split(","):
                command = command.strip()
                if not command or any(char.isspace() for char in command):
                    parser.error(f"invalid command in {source} allowed_commands: {token!r}")
                if command not in commands:
                    commands.append(command)

    if not commands:
        parser.error(f"{source} allowed_commands cannot be empty")
    return commands


def normalize_disabled_tools(
    parser: argparse.ArgumentParser, value: Any, source: str
) -> list[str]:
    """Validate and de-duplicate disabled tool names while preserving order."""
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        parser.error(f"{source} disableTools must be a list of strings")

    disabled_tools = []
    for tool in values:
        if not isinstance(tool, str):
            parser.error(f"{source} disableTools must contain only strings")
        tool = tool.strip()
        if not tool or any(char.isspace() for char in tool):
            parser.error(f"invalid tool name in {source} disableTools: {tool!r}")
        if tool not in disabled_tools:
            disabled_tools.append(tool)
    return disabled_tools


def config_value(parser: argparse.ArgumentParser, config: dict[str, Any], key: str, default: Any) -> Any:
    """Return a validated scalar setting from the configuration."""
    value = config.get(key, default)
    if key == "host" and not isinstance(value, str):
        parser.error("config host must be a string")
    if key == "port" and (not isinstance(value, int) or isinstance(value, bool)):
        parser.error("config port must be an integer")
    if key == "quiet" and not isinstance(value, bool):
        parser.error("config quiet must be a boolean")
    if key == "auditlog" and value is not None and not isinstance(value, str):
        parser.error("config auditlog must be a string or null")
    return value


def resolve_working_directory(
    parser: argparse.ArgumentParser,
    value: Optional[str],
    input_fn=input,
) -> Path:
    """Prompt for and validate the directory used by all local tools.

    An omitted value is requested interactively, with the process's current
    directory as the default.  When standard input is unavailable (for example,
    when started as a service), the default is selected without failing startup.
    """
    default = Path.cwd()
    if value is None:
        try:
            value = input_fn(f"Working directory [{default}]: ").strip()
        except EOFError:
            value = ""
        if not value:
            value = str(default)

    try:
        directory = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        parser.error(f"invalid working directory {value!r}: {exc}")

    if not directory.is_dir():
        parser.error(f"working directory is not a directory: {directory}")
    return directory


if __name__ == "__main__":
    # Set level=logging.DEBUG here to include the original diagnostic messages.
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    parser = ArgumentParser(
        description="MCP server offering shell commands, file write and url fetch",
        fromfile_prefix_chars='@'
    )
    parser.add_argument(
        "--cwd",
        type=str,
        metavar="PATH",
        help="working directory; prompts with the current directory by default",
    )
    parser.add_argument(
        "--conf",
        default="config.json",
        metavar="FILE",
        help="optional JSON config file (default: config.json)",
    )    
    parser.add_argument("--host", type=str, help="server bind host")
    parser.add_argument("--port", type=int, help="server bind port")
    parser.add_argument(
        "--allow",
        action="append",
        nargs="+",
        metavar="COMMAND",
        help="allowed command(s); may be repeated and overrides allowed_commands from config",
    )
    parser.add_argument(
        "--disableTools",
        metavar="TOOL[,TOOL...]",
        help="comma-separated tool names to omit; overrides disableTools from config; case sensitive and exact name match is required",
    )
    parser.add_argument(
        "--auth",
        metavar="auth_token",
        help="auth token for Bearer authentication",
    )    
    parser.add_argument("--sse", action='store_true', help="use sse transport")
    parser.add_argument("--quiet", action="store_true", default=None, help="do not print audit events to stdout")
    parser.add_argument(
        "--auditlog", "--auditlogfile", "--auditlologfile.log",
        dest="auditlog", metavar="FILE", help="append session audit events to FILE",
    )
    args = parser.parse_args()

    config = load_config(parser, args.conf)

    quiet = args.quiet if args.quiet is not None else config_value(parser, config, "quiet", False)
    configured_auditlog = config.get("auditlog", config.get("auditlogfile"))
    if configured_auditlog is not None and not isinstance(configured_auditlog, str):
        parser.error("config auditlog must be a string or null")
    auditlog = args.auditlog if args.auditlog is not None else configured_auditlog
    configure_audit(quiet, auditlog)

    host = args.host if args.host is not None else config_value(parser, config, "host", DEFAULT_HOST)
    port = args.port if args.port is not None else config_value(parser, config, "port", DEFAULT_PORT)
    if not 1 <= port <= 65535:
        parser.error("port must be in the range 1..65535")

    if args.auth:
        auth = args.auth
    elif config.get("auth"):
        auth = config.get("auth")
    else:
        auth = None        

    configured_commands = config.get("allowed_commands", DEFAULT_ALLOWED_COMMANDS)
    if args.allow is not None:
        ALLOWED_COMMANDS = normalize_commands(parser, args.allow, "--allow")
    else:
        ALLOWED_COMMANDS = normalize_commands(parser, configured_commands, "config")

    configured_disabled_tools = config.get("disableTools", [])
    if args.disableTools is not None:
        disable_tools = normalize_disabled_tools(parser, args.disableTools, "--disableTools")
    else:
        disable_tools = normalize_disabled_tools(parser, configured_disabled_tools, "config")

    log.debug(args)
    cwd = resolve_working_directory(parser, args.cwd)
    log.info("working directory: %s", cwd)
    log.info("server address: %s:%s", host, port)

    mcp = create_server(host, port, ALLOWED_COMMANDS, auth, disable_tools)
    asyncio.run(listtools(mcp))
    log.info("allowed commands: %s", ALLOWED_COMMANDS)

    if args.sse:
        log.info("running SSE transport")
        mcp.run(
            transport="sse",
            log_level="INFO",
            host=host,
            port=port,
            middleware=CORS_MIDDLEWARE,
        )
    else:
        log.info("running streamable-http transport")
        mcp.run(
            transport="streamable-http",
            log_level="INFO",
            host=host,
            port=port,
            middleware=CORS_MIDDLEWARE,
        )
