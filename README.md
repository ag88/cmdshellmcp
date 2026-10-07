# cmdshellmcp

`cmdshellmcp` is a constrained command-shell [MCP](https://modelcontextprotocol.io/) server for AI agents. It exposes a small allowlisted Unix command set, somewhat safe file operations, patch application, and URL fetching so an MCP client can perform limited local tasks without unrestricted shell access.

The server is implemented in Python and runs as an MCP server using the `fastmcp` package. By default it listens on `127.0.0.1:8003` using the streamable HTTP transport unless `--sse` is selected.

Added setup files for a full featured Docker Debian based sandbox system container with `cmdshellmcp` (setup scripts, guides [DOCKER.md](DOCKER.md), Dockerfile, `cmdshellmcp.service` unit file that runs `cmdshellmcp` in the container).

## [!CAUTION]
**This server provides remote command execution (RCE), which is normally considered a critical security vulnerability.** Allow-listing powerful commands—such as `bash`, `sh`, `python`, `perl`, `sudo`, `docker`, or commands capable of writing files—may allow an attacker or untrusted LLM to bypass intended restrictions and take control of the system. For example, allowing `python` or `bash` can effectively permit arbitrary code execution and file access. If the server lacks strong authentication, is reachable by untrusted clients, or is controlled by an untrusted or prompt-injected LLM, it may cause severe damage, including data loss, credential theft, malware installation, or compromise of other systems. Run `cmdshellmcp` \(`cmdshellmcp2.py`\)  only in a protected, disposable sandbox with carefully scoped privileges limited to those required for its intended task and limited access to files, credentials, devices, and networks—for example, an ephemeral Docker container or virtual machine that can be safely destroyed after use.

## Warning

This is a scratch / experimental app that evolved out of using an MCP server (tools) that allow running shell commands for coding purpose.
Unfortunately, for such a purpose, one often needs to provide the LLM (model) client with rather powerful shell commands to "do its job"
for a particular scope / context / intent.

The default allowed commands are not necessarily safe, i.e. LLM agents or practically clients calling
the MCP api can 'escape' and do things outside a context e.g. the working directory defined with `--cwd`
option. It also doesn't validate the arguments if they are after all safe. 

There are also tools (MCP functions exposed) that expose write and file modification ops, including
executing shell commands.

* Authentication is enabled by default. Keep the randomly generated token, or set
  a fixed **auth_token** using the **auth** field in the config file or `--auth`.
  Using `--noauth` practically means you are giving remote command execution
  (RCE) to any \(including possibly malicious or rogue\) clients that can reach the server.
* Run this as an unprivileged user. Running as `root` is at best foolish
* Do not use this with untrusted clients or untrusted LLMs
* Use it in a disposable sandbox e.g. a standalone docker container or virtual machine that you can afford to throw away including the contents
* Review the allow list in `cmdshellmcp.json` and the hardcoded defaults, revise them before using.

## Features

- Allowlisted shell execution for a curated set of commands
- File read/write/list/copy/move/rename/delete operations and basic directory creation/removal under a configured working directory
- Transactional text-file editing with `sed`, numbered backups, and unified diffs
- Unified diff patch application via `patch`
- HTTP fetch support with optional HTML prettification
- Bearer token authentication with a secure, randomly generated token by default
- Audit logging to stdout and/or a file
- Path restrictions to prevent escaping the current working directory
- Setup files for a delightful, full featured Docker Debian based sandbox system container with `cmdshellmcp` (setup scripts, guides [DOCKER.md](DOCKER.md), Dockerfile, `cmdshellmcp.service` unit file that runs `cmdshellmcp` in the container).

## AI use in this repo

This app and its contents e.g. this page, is created with the aid of LLM (large language models) such as
- ChatGPT 5.6 sol (light), Codex
- Github Co-pilot MAI-Code-1.1-Flash

Initial codes is written by the author and refactored with aid of the LLMs and updates is also done partly manually.
After features are added or changed, additional tests is often done by manually running them e.g.
in [llama.cpp's llama-server Web UI](https://github.com/ggml-org/llama.cpp#quick-start)

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

The `editFile` tool also requires GNU `sed` (including its `--sandbox` option)
and `diff` to be installed on the server. These programs are invoked directly by
the dedicated tool and do not need to appear in `allowed_commands`.

## Configuration

The server selects one JSON configuration file in this order (files are not merged):

1. The file specified by `--conf`, when given (no automatic search).
2. `/etc/cmdshellmcp.d/cmdshellmcp.json`.
3. `/usr/local/python/cmdshellmcp/cmdshellmcp.json`.
4. `cmdshellmcp.json` in the process's current directory.
5. If no file exists, command-line settings and internal defaults apply.

A missing explicit `--conf` file also uses command-line settings and internal
defaults; it does not search other locations. An invalid selected JSON file
causes startup to fail. Command-line options override their configuration keys.

Example:

```json
{
  "host": "127.0.0.1",
  "port": 8003,
  "cwd": null,
  "shellnopathchk": false,
  "editdelbk": false,
  "quiet": false,
  "auditlog": null,
  "disableTools": ["writeFile", "editFile", "applyPatch", "copyFile", "moveRenameFile", "deleteFile", "mkdir", "rmdir"],
  "allowed_commands": [
    "ls", "pwd", "date", "cat", "grep", "egrep",
    "whoami", "head", "tail", "sed", "wc", "file", "du", "df",
    "free", "ps", "uname", "hostname", "uptime", "w", "last",
    "mkdir", "cp", "mv", "awk"
  ],
  "auth": "_my_secret_auth_token_"
}
```

Supported configuration keys:

- `host`: bind host; default `127.0.0.1`
- `port`: bind port; default `8003`
- `cwd`: working directory path; omitted or `null` prompts, then defaults to the process's current directory
- `shellnopathchk`: boolean; bypasses shell argument path checks when `true`; default `false`
- `editdelbk`: boolean; deletes successful edit backups after composing the diff when `true`; default `false`
- `quiet`: suppresses audit output to stdout when `true`
- `auditlog`: optional path to an audit log file
- `allowed_commands`: list of commands permitted for execution
- `disableTools`: list of MCP tool names to omit from the server, case sensitive and exact name match is required
- `auth`: optional fixed bearer token string; when omitted, a random token is generated at startup

## Running the server

Start the server with the default settings:

```bash
python cmdshellmcp2.py
```

When neither `--cwd` nor config `cwd` supplies a path, the server prompts for a
working directory and shows the process's current directory as the default. Press Enter to accept it. If standard
input is unavailable (for example, when running as a service), the current
directory is selected automatically. The server then starts on `127.0.0.1:8003`
using the streamable HTTP transport.

### SSE mode

```bash
python cmdshellmcp2.py --sse
```

This runs the server on the SSE transport instead of streamable HTTP.

### Browser clients and CORS

Both HTTP transports include CORS middleware for browser-based MCP clients. The
server accepts requests from any origin, supports the MCP `GET`, `POST`, and
`DELETE` methods and browser preflight `OPTIONS` requests, and exposes the
`mcp-session-id` response header to browser JavaScript.

Because all origins are allowed, do not expose the server to an untrusted
network without authentication and appropriate network controls. To restrict
browser access, replace `allow_origins=["*"]` in `CORS_MIDDLEWARE` with the
specific trusted origins.

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

Authentication is enabled by default. If neither `--auth` nor an `auth` value in
the configuration file supplies a fixed token, the server generates a
cryptographically secure, base64-encoded token and displays it in the startup
log. The generated token is shown in bold when stdout supports ANSI styling.
Configure the MCP client to send that value as its bearer token.

To use a fixed token instead:

```bash
python cmdshellmcp2.py --auth my-secret-token
```

To explicitly run without authentication:

```bash
python cmdshellmcp2.py --noauth
```

`--auth` and `--noauth` are mutually exclusive. `--noauth` also overrides an
`auth` value in the configuration file. Disabling authentication is unsafe on
any network that is not completely trusted.

### Audit logging

```bash
python cmdshellmcp2.py --quiet --auditlog /tmp/cmdshellmcp.log
```

- `--quiet` disables audit logging to stdout.
- `--auditlog` appends audit events to the specified file.

## A full featured Docker Debian based sandbox system container with cmdshellmcp

This project provides setup files for a delightful, full featured Docker Debian based sandbox system container with `cmdshellmcp` (setup scripts, guides [DOCKER.md](DOCKER.md), Dockerfile, `cmdshellmcp.service` unit file that runs `cmdshellmcp` in the container).

Using the docker sandbox container gives you a "system in a system" experience, e.g. that Debian boots up within the container just like a full Unix / Linux system. It gives you 3 userids within the container:

* `root`: (superuser)
* `admin` (this is for you / human user), `admin` has `sudo` access, i.e. practically can do anything `root` does, install files, change system configs, permissions etc
* `codeagent`: `cmdshellmcp` runs under this user, in the user's `/home/codeagent` home directory. `codeagent` has no sudo access.

For more details review [DOCKER.md](DOCKER.md)

## Command-line options

```bash
python cmdshellmcp2.py [--cwd PATH] [--host HOST] [--port PORT] \
  [--allow COMMAND [COMMAND ...]] [--conf FILE] [--auth TOKEN | --noauth] \
  [--disableTools TOOL[,TOOL...]] [--editdelbk] [--shellnopathchk] [--sse] [--quiet] \
  [--auditlog FILE]
```

Options:

- `--cwd`: working directory used for file and shell operations; when omitted,
  use config `cwd`, otherwise prompt with the process's current directory as the default
- `--host`: server bind host
- `--port`: server bind port
- `--allow`: override the allowlist for the current process; may be repeated
- `--disableTools`: comma-separated MCP tool names to omit; overrides the
  `disableTools` list from the config file; case sensitive and exact name match is required
- `--editdelbk`: delete the numbered backup after `editFile` has completed
  successfully and composed its unified diff response; overrides config `editdelbk`
- `--shellnopathchk`: bypass `_local_path_error` checks on `cmdshell` arguments
  for this server process; overrides config `shellnopathchk`; file tools continue to check their paths
- `--conf`: explicit JSON config file path; when omitted, search the locations
  listed in [Configuration](#configuration)
- `--auth`: use a fixed bearer token instead of generating one
- `--noauth`: explicitly disable bearer authentication; mutually exclusive with
  `--auth` and unsafe on untrusted networks
- `--sse`: use SSE transport instead of streamable HTTP
- `--quiet`: suppress audit output on stdout
- `--auditlog`: write audit logs to a given file

### Allowlist override

```bash
python cmdshellmcp2.py --allow ls pwd date whoami cat grep
```

This overrides `allowed_commands` from the config file for that process.

### Disabling tools

```bash
python cmdshellmcp2.py --disableTools writeFile,editFile,applyPatch,fetch
```

This prevents matching tools from being registered by the server. *Tool names
are case-sensitive and must match the function names in the [MCP tools exposed to AI agents](#MCP-tools-exposed-to-AI-agents) section  below*. 
The command-line option overrides the `disableTools` list from the config file.

## Security model

This server is intentionally restricted. It is designed to be somewhat safe in a controlled environment rather than as a general unrestricted shell.

Security features include:

- Shell commands must be explicitly present in the allowlist
- Command names are checked before execution
- The `cmdshell` tool expects a command name and argument array, not a raw shell string
- Piping is not supported by design
- `cmdshell` checks every argument with `_local_path_error` after escape decoding
  and removal of surrounding double quotes, before expanding globs. Expanded
  filenames are also checked before execution. Empty arguments, absolute paths,
  and paths containing a `..` component are rejected by default.
- Start the server with `--shellnopathchk` or set config `shellnopathchk` to `true` when shell commands need access to
  shared resources such as `/usr/share` or `/usr/include`. This bypass applies
  only to `cmdshell`; the command allowlist remains enforced.
- File tools perform basic `_local_path_error` checks intended to keep file paths within `--cwd`: absolute paths and paths containing `..` are rejected
- These are basic lexical path checks rather than a complete filesystem sandbox.
  They do not resolve symlinks or interpret command-specific options such as
  `--file=/absolute/path`, scripts, or expressions. Choose the command allowlist
  carefully for your intended usage.
- `editFile` accepts only a small allowlist of non-file-selecting `sed` options,
  runs GNU `sed` in sandbox mode, and does not invoke a shell
- `editFile` writes successful output to a temporary file before atomically
  replacing the source; failed `sed` runs leave the source unchanged
- Patch application blocks dangerous path-changing options

In short: the shell is a narrow sandbox for controlled read/write operations, not a full-host terminal.

### Working directory resolution

The directory used by shell and file tools is selected in this order:

1. `--cwd PATH`, when given.
2. `cwd` from the selected configuration file, when non-null.
3. The path entered at the working directory prompt.
4. The process's current directory if the prompt is left blank or stdin reaches EOF.

An explicit or configured path skips the prompt. Paths must exist and be
directories; `~` is expanded and paths are resolved to absolute paths. Relative
paths are resolved against the process's current directory, including paths
from a configuration file (not against the config file's location). Invalid
paths fail startup rather than falling through to another directory.
Configuration discovery also uses the process's current directory, before
working directory selection. For services, set `--cwd` or config `cwd` to make
the tool directory independent of the service's launch directory. This directory
scopes local operations but does not provide a complete filesystem sandbox.

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
- Arguments are checked with `_local_path_error` by default, including decoded
  arguments and expanded glob matches. This also checks arguments used as flags
  or text; empty arguments, absolute paths, and `..` path components are rejected.
- Glob patterns may be expanded automatically relative to `--cwd`.
- Quoted globs can be passed literally to prevent expansion.
- Pipes and redirects are not allowed
- To bypass only the shell argument path checks, start the server with:

  ```bash
  python cmdshellmcp2.py --cwd /path/to/project --shellnopathchk
  ```

  The MCP tool's parameters remain `command` and `args`; the bypass is configured
  at server startup, and file tools still enforce their path checks.

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

### 5. `copyFile(source, destination)`

Copies a regular file beneath the configured working directory using Python's
standard-library file operations. Both source and destination are checked with
`_local_path_error`.

Example:

```python
copyFile("src/example.py", "src/example.py.copy")
```

### 6. `moveRenameFile(source, destination)`

Moves or renames a regular file using `os.rename`. Both source and destination
are checked with `_local_path_error`.

Example:

```python
moveRenameFile("draft.txt", "archive/draft.txt")
```

### 7. `deleteFile(file)`

Deletes a regular file using `os.remove` after checking the path with
`_local_path_error`.

Example:

```python
deleteFile("obsolete.txt")
```

### 8. `mkdir(path)`

Creates one directory using `os.mkdir` after checking the path with
`_local_path_error`.

Example:

```python
mkdir("build")
```

The parent directory must already exist.

### 9. `rmdir(path)`

Removes one empty directory using `os.rmdir` after checking the path with
`_local_path_error`.

Example:

```python
rmdir("build")
```

The directory must be empty; this tool does not recursively delete directory
contents.

### 10. `editFile(file, script, args=None)`

Edits an existing text file beneath the configured working directory using GNU
`sed`. The dedicated `script` parameter is the only source of the editing
program; the command is executed with an argument list rather than through a
shell.

Parameters:

- `file`: relative path of an existing regular file beneath `cwd`
- `script`: a `sed` editing expression or program, such as `s/old/new/g`,
  `/pattern/d`, or `10,20s/old/new/g`
- `args`: optional safe `sed` options, such as `-n` or `-E`; options that enable
  in-place editing, provide another expression or program file, or select
  additional input/output files are rejected

Example:

```python
editFile("src/example.py", "s/old_name/new_name/g")
```

Before running `sed`, the tool copies the source to the next unused numbered
backup. For example, the first edit above creates `src/example.py.bk1`; if that
name exists, it uses `.bk2`, then `.bk3`, and so on. Existing backups are never
overwritten.

`sed` writes its proposed result to memory while running in sandbox mode, which
blocks GNU `sed` commands that read files, write files, or execute programs. The
source is replaced from a same-directory temporary file only after `sed` exits
successfully. A failed `sed` run leaves the source unchanged and removes the new,
unneeded backup. If replacement has begun and a later step fails, the backup is
retained for recovery.

On success, the response identifies the backup and includes the output of:

```bash
diff -u src/example.py.bk1 src/example.py
```

The backup is the old version and the current file is the new version. A no-op
edit is reported explicitly and still retains its numbered backup.

Start the server with `--editdelbk` or set config `editdelbk` to `true` to remove each numbered backup after a
successful edit. The tool first runs `diff` and composes the complete response,
so the returned unified diff remains available even though the backup has been
deleted. The success message identifies the deleted backup. Backups are still
retained when replacement or diff generation fails, so they remain available
for recovery. If backup deletion itself fails, the response begins with `Error:`
and reports that the edit completed but the backup remains.

> **Security note:** backups contain the complete pre-edit file, including any
> secrets it held. Unless `--editdelbk` or config `editdelbk` is enabled, they remain on disk after
> successful edits. Protect and remove them according to the same retention
> policy as the source file.

### 11. `applyPatch(file, diff, pnum=2, args=None)`

Applies a diff to one explicitly named file using GNU `patch`. The diff is
normally a unified diff and is passed directly to `patch` over standard input.

Parameters:

- `file`: existing regular file beneath the configured current directory
- `diff`: patch text, normally a unified diff
- `pnum`: number of leading path components to strip, corresponding to GNU
  patch's `-pNUM`; default `2`
- `args`: optional allowlisted GNU patch flags; options that can select another
  target, input, output, directory, backup/reject destination, or strip value
  are rejected

Example:

```python
applyPatch(
    file="src/example.py",
    pnum=2,
    diff="--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n",
)
```

The explicit `file` is authoritative; filenames embedded in the diff are not
used to choose a target. Absolute paths and parent traversal are rejected, and
the resolved target must remain within the configured current directory.

### 12. `fetch(url, prettify=False)`

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

## Typical use cases

- Inspecting repository and filesystem state
- Reading source files and logs
- Writing small generated files or config changes
- Making reviewable text substitutions with automatic backups
- Applying small patches
- Fetching documentation or data from the web
- Running a limited set of somewhat safe diagnostics

This server is best used when an AI agent needs controlled local access without being given unrestricted system commands.
