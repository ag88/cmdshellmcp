# cmdshellmcp

`cmdshellmcp` is a constrained command-shell [MCP](https://modelcontextprotocol.io/) server for AI agents. It exposes a small allowlisted Unix command set, somewhat safe file operations, patch application, and URL fetching so an MCP client can perform limited local tasks without unrestricted shell access.

The server is implemented in Python and runs as an MCP server using the `fastmcp` package. By default it listens on `127.0.0.1:8003` using the streamable HTTP transport unless `--sse` is selected.

## Warning

This is a scratch / experimental app that evolved out of using an MCP server (tools) that allow running shell commands for coding purpose.

The default allowed commands are not necessarily safe, i.e. LLM agents or practically clients calling
the MCP api can 'escape' and do things outside a context e.g. the working directory defined with `--cwd`
option. It also doesn't validate the arguments if they are after all safe. 

There are also tools (MCP functions exposed) that expose write and file modification ops, including
executing shell commands.

* do not use this with untrusted clients or untrusted LLMs, use it in a disposable sandbox e.g. a standalone docker
  container that you can afford to throw away including the contents
* review the allow list in `config.json` and the hardcoded defaults, revise them before using.

## Features

- Allowlisted shell execution for a curated set of commands
- File read/write/list operations under a configured working directory
- Unified diff patch application via `patch`
- HTTP fetch support with optional HTML prettification
- Optional bearer token authentication
- Audit logging to stdout and/or a file
- Path restrictions to prevent escaping the current working directory

## Installation

1. Clone the repository.
2. Create and activate a virtual environment if desired.
3. Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastmcp requests beautifulsoup4
```

If your environment uses a `requirements.txt`, you can also install from there:

```bash
pip install -r requirements.txt
```

## Configuration

The server reads an optional JSON configuration from `config.json` by default.

Example:

```json
{
  "host": "lxrouter.local",
  "port": 8003,
  "quiet": false,
  "auditlog": null,
  "allowed_commands": [
    "ls", "pwd", "date", "cat", "grep", "egrep",
    "whoami", "head", "tail", "sed", "wc", "file", "du", "df",
    "free", "ps", "uname", "hostname", "uptime", "w", "last",
    "mkdir", "cp", "mv", "awk"
  ]
}
```

Supported configuration keys:

- `host`: bind host; default `127.0.0.1`
- `port`: bind port; default `8003`
- `quiet`: suppresses audit output to stdout when `true`
- `auditlog`: optional path to an audit log file
- `allowed_commands`: list of commands permitted for execution
- `auth`: optional bearer token string

## Running the server

Start the server with the default settings:

```bash
python cmdshellmcp2.py
```

When `--cwd` is omitted, the server prompts for a working directory and shows the
process's current directory as the default. Press Enter to accept it. If standard
input is unavailable (for example, when running as a service), the current
directory is selected automatically. The server then starts on `127.0.0.1:8003`
using the streamable HTTP transport.

### SSE mode

```bash
python cmdshellmcp2.py --sse
```

This runs the server on the SSE transport instead of streamable HTTP.

### Custom working directory

```bash
python cmdshellmcp2.py --cwd /path/to/project
```

This sets the working directory used by file and shell tools without prompting.
The path must exist and must be a directory; `~` is expanded and the selected
path is normalized to an absolute path.

### Custom host and port

```bash
python cmdshellmcp2.py --host 0.0.0.0 --port 9000
```

### Authentication

```bash
python cmdshellmcp2.py --auth my-secret-token
```

If present, the server requires a bearer token.

### Audit logging

```bash
python cmdshellmcp2.py --quiet --auditlog /tmp/cmdshellmcp.log
```

- `--quiet` disables audit logging to stdout.
- `--auditlog` appends audit events to the specified file.

## Command-line options

```bash
python cmdshellmcp2.py [--cwd PATH] [--host HOST] [--port PORT] \
  [--allow COMMAND [COMMAND ...]] [--conf FILE] [--auth TOKEN] \
  [--sse] [--quiet] [--auditlog FILE]
```

Options:

- `--cwd`: working directory used for file and shell operations; when omitted,
  prompt with the process's current directory as the default
- `--host`: server bind host
- `--port`: server bind port
- `--allow`: override the allowlist for the current process; may be repeated
- `--conf`: JSON config file path (default: `config.json`)
- `--auth`: bearer token required for authentication
- `--sse`: use SSE transport instead of streamable HTTP
- `--quiet`: suppress audit output on stdout
- `--auditlog`: write audit logs to a given file

### Allowlist override

```bash
python cmdshellmcp2.py --allow ls pwd date whoami cat grep
```

This overrides `allowed_commands` from the config file for that process.

## Security model

This server is intentionally restricted. It is designed to be somewhat safe in a controlled environment rather than as a general unrestricted shell.

Security features include:

- Shell commands must be explicitly present in the allowlist
- Command names are checked before execution
- The `cmdshell` tool expects a command name and argument array, not a raw shell string
- Piping is not supported by design
- File tools reject absolute paths and paths containing `..`
- Writes are limited to locations beneath the configured `cwd`
- Patch application blocks dangerous path-changing options
- Diff path validation prevents escaping the working directory

In short: the shell is a narrow sandbox for controlled read/write operations, not a full-host terminal.

## MCP tools exposed to AI agents

The server registers the following tools:

### 1. `cmdshell(command, args)`

Runs one configured Unix command with arguments.

Parameters:

- `command`: the command name, which must appear in the allowlist
- `args`: list of arguments/flags to pass to the command

Example:

```python
cmdshell("ls", ["-la"])
cmdshell("grep", ["-R", "needle", "."])
```

Notes:

- The command name must be in the allowlist.
- Arguments are passed as a list, reducing shell injection risk.
- Glob patterns may be expanded automatically.
- Quoted globs can be passed literally to prevent expansion.

### 2. `writeFile(file, text, append=False, newline=True)`

Writes text to a file beneath the current working directory.

Parameters:

- `file`: a relative path under `cwd`
- `text`: content to write
- `append`: if `true`, append instead of overwrite
- `newline`: append a trailing newline when `true`

Example:

```python
writeFile("notes.txt", "hello from the agent")
```

This is restricted to relative paths below the configured working directory.

### 3. `readFile(file)`

Reads a UTF-8 text file beneath the current working directory.

Example:

```python
readFile("README.md")
```

### 4. `listFiles(path=".")`

Lists the entries in a directory beneath the configured working directory.

Example:

```python
listFiles(".")
listFiles("src")
```

Returns a newline-separated list of entries, with `/` appended for directories.

### 5. `applyPatch(text, args=None, context=2)`

Applies a unified or context diff using the system `patch` command.

Parameters:

- `text`: patch text to apply
- `args`: optional extra patch arguments
- `context`: fuzz level for patch matching; default `2`

Example:

```python
applyPatch(
    "--- a/file.txt\n+++ b/file.txt\n@@\n-old\n+new\n"
)
```

The function blocks dangerous path-changing patch flags and rejects absolute paths.

### 6. `fetch(url, prettify=False)`

Fetches a URL using `requests`. If `prettify` is `true`, it parses the HTML with BeautifulSoup and pretty-prints it.

Example:

```python
fetch("https://example.com")
fetch("https://example.com", prettify=True)
```

## Example startup

```bash
python cmdshellmcp2.py \
  --cwd /workspace/project \
  --host 0.0.0.0 \
  --port 8003 \
  --auth mytoken \
  --allow ls pwd date cat grep head tail wc
```

This starts a server with a fixed working directory, bind host, port, authentication, and a minimal command allowlist.

## Notes

- Default transport: `streamable-http`
- Default host: `127.0.0.1`
- Default port: `8003`
- Default allowlist is built from a small set of somewhat safe commands
- The server does not support arbitrary shell pipelines or unrestricted command execution

## Typical use cases

- Inspecting repository and filesystem state
- Reading source files and logs
- Writing small generated files or config changes
- Applying small patches
- Fetching documentation or data from the web
- Running a limited set of somewhat safe diagnostics

This server is best used when an AI agent needs controlled local access without being given unrestricted system commands.


