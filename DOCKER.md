# Docker system sandbox for cmdshellmcp

This example builds a Debian 13 container with systemd as PID 1, interactive user accounts, and `cmdshellmcp` running as a service under an ordinary user. Docker supplies native bridge networking and port publishing; no manually injected network interface or DHCP client is needed.

## Prerequisite: install Docker Engine on an Ubuntu host

The examples in this guide require a Linux host with Docker Engine and the Docker CLI. On a supported 64-bit Ubuntu release, install the current stable Docker Engine from Docker's official `apt` repository with the following commands. Run them in a terminal from an account with `sudo` access:

```bash
# Add Docker's official signing key.
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the Docker repository for this Ubuntu release and architecture.
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

# Install Docker Engine, the CLI, containerd, Buildx, and Compose.
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

Confirm that the daemon is running and launch Docker's test container:

```bash
sudo systemctl status docker --no-pager
sudo docker run --rm hello-world
```

These commands intentionally use `sudo docker`. If you later configure Docker for use without `sudo`, understand that membership in the `docker` group grants root-level privileges. For other Ubuntu versions, upgrades, conflicting packages, rootless operation, or troubleshooting, follow Docker's maintained [Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/) and [Linux post-installation guide](https://docs.docker.com/engine/install/linux-postinstall/).

Use a Linux Docker Engine host with the systemd/cgroup support required by `1stRun.sh`. The supplied run script uses `--privileged`, the host cgroup namespace, and a writable host cgroup mount. These permissions substantially weaken host isolation: use a disposable host or VM when handling untrusted agent activity. The human administrator's sudo password controls sudo access, but does not make this privileged container a strong security boundary. Do not mount the Docker socket or sensitive host directories into the sandbox.

## 1. Prepare the local build files

Run the scripts from the local `cmdshellmcp` repository root. These files must be in the build context:

```text
Dockerfile_cmdshellmcp
cmdshellmcp.service
cmdshellmcp2.py
cmdshellmcp.json
requirements.txt
0dockerBuild.sh
1stRun.sh
```

The Dockerfile copies the local project files; it does not clone or download the repository. Building still needs access to the Debian image, APT repositories, and Python package sources unless those dependencies are available locally.

Commands labelled **Host** run on your Docker host. Commands labelled **Container — admin** run inside the container as your human administrator. If your host requires sudo for Docker, prefix host Docker commands with `sudo`.

## 2. Review the Dockerfile

Review `Dockerfile_cmdshellmcp` before building. Add any tools you want installed to the `apt-get install` package list, one per line. For example, add Vim alongside Nano:

```dockerfile
        nano \
        vim \
        python3 \
```

The image installs the following under `/usr/local/python/cmdshellmcp`:

| Path | Purpose |
| --- | --- |
| `cmdshellmcp2.py` | MCP server |
| `cmdshellmcp.json` | Server configuration |
| `requirements.txt` | Python dependencies |
| `venv/` | Python virtual environment with the installed dependencies |

The application and environment are owned by root. The configuration is readable by the `codeagent` group and editable by an administrator through sudo. Merely installing a command does not add it to the server's `allowed_commands` list.

## 3. Review the service and configuration

Review `cmdshellmcp.service` and `cmdshellmcp.json` before building. The supplied service:

- Runs as `User=codeagent` and `Group=codeagent`, without sudo access.
- Uses `/home/codeagent` for both `WorkingDirectory` and the server's `--cwd` argument.
- Uses the virtual environment's Python and the configuration at `/usr/local/python/cmdshellmcp/cmdshellmcp.json`.
- Overrides the configuration's host with `--host 0.0.0.0`, allowing Docker's published port to reach the server.
- Uses the configured port, normally `8003`, and restarts after failure.
- Sends output to the systemd journal and sets `NoNewPrivileges=true`.

Review the allowed commands, disabled tools, and authentication settings in the JSON before connecting an agent. Keep the application port and Docker's container-side port mapping consistent. The default transport is streamable HTTP, with the MCP endpoint normally at `/mcp`.

Both files can also be edited later inside the container; see the maintenance actions below.

## 4. Build the image

**Host**, from the repository root:

```bash
bash 0dockerBuild.sh
```

The supplied script builds `Dockerfile_cmdshellmcp` and tags the image as `cmdshellmcp-image`. Its equivalent Docker command is:

```bash
docker build -f Dockerfile_cmdshellmcp -t cmdshellmcp-image .
```

If you change `IMAGE_NAME` in `0dockerBuild.sh`, make the corresponding change in `1stRun.sh`.

## 5. Review the first-run script and port mapping

Review `1stRun.sh` before creating the container. It names the container `cmdshellmcp`, sets its hostname to `cmdshellmcp`, and currently publishes:

```bash
--publish 8003:8003 \
```

This makes port `8003` available on all host interfaces, subject to host networking and firewall rules. For access only from the host, change it to:

```bash
--publish 127.0.0.1:8003:8003 \
```

To use a different host port while retaining port `8003` inside the container:

```bash
--publish 127.0.0.1:9003:8003 \
```

The format is `[HOST_IP:]HOST_PORT:CONTAINER_PORT`. With the last example, a local MCP client uses `http://127.0.0.1:9003/mcp`. For remote access, use an appropriate reachable host address and protect bearer credentials in transit, for example with a TLS reverse proxy or secure tunnel.

Note that currently `1stRun.sh` publish a spare port 5000. This port is not used by `cmdshellmcp`, you can use it for any purpose from within the the container.
e.g. if an LLM generates a python flask app in the container, you can use this port to run the generated app so that you can view the web page from the host.

```bash
--publish 5000:5000 \
```

`EXPOSE 8003` in the Dockerfile is metadata; `--publish` performs the mapping. Port mappings are fixed when the container is created. Editing `1stRun.sh` does not change an existing container: preserve your data, remove or rename the old container, and create a new one with the desired mapping.

## 6. Create the container and perform first-time setup

**Host**:

```bash
bash 1stRun.sh
```

This calls `docker run -it`: it creates the container and starts it interactively. It does not build the image. Run it once for each new container, after building the image.

You will be prompted for three sets of passwords, each with confirmation. Password input is hidden:

| Account | Role |
| --- | --- |
| `root` | Full administrative access inside the container. |
| `admin` (or the human username you choose) | Your human login account, with password-authenticated sudo access. You can optionally choose its numeric UID. |
| `codeagent` | Ordinary account used by the MCP service and agent tasks. It has no sudo access. |

The human account is added to the `codeagent` group so you can review normally created files in `/home/codeagent`. The home directory uses group access and group inheritance; the service's umask allows group reading of normal new files. Files explicitly created with private permissions may still require sudo to inspect.

Keep the root and human administrator passwords separate from credentials supplied to the agent. The MCP bearer token is a separate credential from all three Linux account passwords.

After setup, systemd boots the container like a small Debian instance and starts `cmdshellmcp.service`. When the console login prompt appears, you can log in as any of the three accounts; normally use your human account for administration. If your host's console setup does not present a login prompt, use the separate-shell commands below.

Setup completion is recorded in the container's writable layer. Stopping and starting the same container preserves accounts, passwords, files, and configuration. Removing it and creating a new one repeats first-time setup.

# Administration cheatsheet

The examples below use container `cmdshellmcp`, image `cmdshellmcp-image`, and human username `admin`. Substitute your chosen names where needed.

## Detach from the interactive console

Press **Ctrl-P, then Ctrl-Q**. Release the first combination before pressing the second. The container continues running.

Use this sequence instead of Ctrl-C, which may forward a signal to the attached process. To log out of a console login session, use `exit`; logging out does not normally shut down systemd.

## Attach to a running container

**Host**:

```bash
docker attach cmdshellmcp
```

This reconnects to the existing main console; it does not open a new shell or a new login session. Detach with Ctrl-P, then Ctrl-Q.

## Open a separate shell

**Host**, while the container is running:

```bash
docker exec -it --user admin --workdir /home/admin cmdshellmcp /bin/bash -l
```

For an agent-account shell:

```bash
docker exec -it --user codeagent --workdir /home/codeagent cmdshellmcp /bin/bash -l
```

For a root maintenance shell:

```bash
docker exec -it --user root cmdshellmcp /bin/bash -l
```

Leave a separate shell with `exit`; the container continues running. `docker exec --user` selects the account without asking for its password. Access to the host Docker daemon therefore already grants control over these container accounts.

## Find the MCP bearer token

**Container — admin**:

```bash
sudo journalctl -u cmdshellmcp.service -b --no-pager
```

Look for `generated auth token:` from the most recent service start. If neither JSON `auth` nor command-line `--auth` is supplied, the server generates a random bearer token. A new token is generated on each service start or restart; update your MCP client accordingly. Do not confuse older tokens in the journal with the current one.

To follow new output:

```bash
sudo journalctl -u cmdshellmcp.service -f
```

If a token is explicitly configured, the generated-token message will not appear. Treat tokens and logs containing them as secrets. When authentication is enabled, clients send `Authorization: Bearer TOKEN`.

## Check the service status

**Container — admin**:

```bash
sudo systemctl status cmdshellmcp.service --no-pager
sudo systemctl is-enabled cmdshellmcp.service
```

Check listening ports:

```bash
ss -ltn
```

## Stop the container from the host

**Host**:

```bash
docker stop cmdshellmcp
```

Docker requests shutdown and waits before forcing termination if necessary. To allow a longer shutdown period:

```bash
docker stop --time 30 cmdshellmcp
```

## Shut down from inside the container

**Container — admin**:

```bash
sudo systemctl poweroff
```

This shuts down the container's systemd instance and stops the container; it does not power off the Docker host.

## Start a stopped container interactively

**Host**:

```bash
docker start -ai cmdshellmcp
```

This starts the existing container and attaches input/output. Completed first-time setup is skipped.

## Start a stopped container in the background

**Host**:

```bash
docker start cmdshellmcp
```

Then connect to its console if desired:

```bash
docker attach cmdshellmcp
```

Alternatively, open a separate shell with `docker exec`. Do not rerun `1stRun.sh` to start the same container: `docker run` creates a new one and its name would conflict.

## Restart the container

**Host**:

```bash
docker restart cmdshellmcp
```

This also restarts the MCP service and changes any automatically generated bearer token.

## List containers

**Host**, running containers:

```bash
docker ps
```

All containers, including stopped ones:

```bash
docker ps -a
```

## List images

**Host**:

```bash
docker image ls
```

Rebuilding an image does not update containers already created from it. Create a new container to use the rebuilt image.

## Inspect port mappings and resource usage

**Host**:

```bash
docker port cmdshellmcp
docker stats --no-stream cmdshellmcp
docker inspect --format '{{.State.Status}}' cmdshellmcp
```

## Read container console output

**Host**:

```bash
docker logs --tail 100 cmdshellmcp
```

The MCP service writes to the journal, so use `journalctl` for its output. From the host:

```bash
docker exec cmdshellmcp journalctl -u cmdshellmcp.service -b --no-pager
```

## Edit the systemd service

**Container — admin**:

```bash
sudo cp -a /etc/systemd/system/cmdshellmcp.service /etc/systemd/system/cmdshellmcp.service.bak
sudo nano /etc/systemd/system/cmdshellmcp.service
sudo systemctl daemon-reload
sudo systemctl restart cmdshellmcp.service
sudo systemctl status cmdshellmcp.service --no-pager
```

Use the exact filename `cmdshellmcp.service`. Changes to the unit need `daemon-reload` before restart. If you change the working directory, update both `WorkingDirectory` and `--cwd`, and ensure `codeagent` can access it. The service's `--host 0.0.0.0` takes precedence over JSON `host`; remove or change that argument if you want different binding behavior.

## Edit the MCP configuration

**Container — admin**:

```bash
sudo cp -a /usr/local/python/cmdshellmcp/cmdshellmcp.json /usr/local/python/cmdshellmcp/cmdshellmcp.json.bak
sudo nano /usr/local/python/cmdshellmcp/cmdshellmcp.json
sudo /usr/local/python/cmdshellmcp/venv/bin/python -m json.tool /usr/local/python/cmdshellmcp/cmdshellmcp.json > /dev/null
sudo systemctl restart cmdshellmcp.service
```

Configuration-only changes need a service restart, but not `daemon-reload`. JSON syntax validation does not validate every application setting; check service status and the journal after restarting.

To use a separate configuration file, create it with appropriate permissions and change the unit's `--conf` argument, then reload the units and restart the service. Changing the server's internal port also requires a matching Docker container-side port mapping.

## Stop, start, or disable the MCP service

**Container — admin**:

```bash
sudo systemctl stop cmdshellmcp.service
sudo systemctl start cmdshellmcp.service
sudo systemctl restart cmdshellmcp.service
```

To prevent automatic startup and stop it now:

```bash
sudo systemctl disable --now cmdshellmcp.service
```

To restore automatic startup and start it now:

```bash
sudo systemctl enable --now cmdshellmcp.service
```

## Install additional packages

**Container — admin**:

```bash
sudo apt-get update
sudo apt-get install vim
```

This changes only the current container. Add the package to the Dockerfile and rebuild if future containers should include it. Keep `/usr/local/python/cmdshellmcp` owned by root when updating application files or Python dependencies.

## Change account passwords

**Container — admin**, your own password:

```bash
passwd
```

Change the other accounts' passwords:

```bash
sudo passwd root
sudo passwd codeagent
```

## Copy files between host and container

**Host**:

```bash
docker cp cmdshellmcp:/home/codeagent ./codeagent-backup
docker cp ./example.txt cmdshellmcp:/home/codeagent/example.txt
```

Check ownership after copying files into the agent's home. For example:

```bash
docker exec --user root cmdshellmcp chown codeagent:codeagent /home/codeagent/example.txt
```

Files and package installations survive stop/start, but are lost when this container is removed. The supplied script does not mount persistent storage for `/home/codeagent`. Copy out important work before removal. If you add a bind mount or volume, review its permissions and only expose host data the agent needs. `/run` and `/tmp` are temporary mounts and are cleared across container restarts.

## Delete the container

**Host**, after backing up anything you need:

```bash
docker stop cmdshellmcp
docker rm cmdshellmcp
```

This deletes the container's writable layer, including account setup and files stored there. It does not delete the image. A subsequent `bash 1stRun.sh` creates a fresh container and prompts for setup again.

## Delete the image

**Host**, after removing containers that use it:

```bash
docker image rm cmdshellmcp-image
```

Docker may refuse removal while containers still reference the image. Rebuild with `bash 0dockerBuild.sh` when needed.

## Troubleshoot startup

**Host**:

```bash
docker ps -a
docker logs --tail 100 cmdshellmcp
```

If first-time setup was interrupted, resume with `docker start -ai cmdshellmcp`. The entrypoint retains the selected administrator account so setup can continue.

If systemd is running but the MCP server is unavailable:

```bash
docker exec cmdshellmcp systemctl status cmdshellmcp.service --no-pager
docker exec cmdshellmcp journalctl -u cmdshellmcp.service -b --no-pager
docker port cmdshellmcp
```

Check the JSON, installed dependencies, working-directory permissions, server bind address, port mapping, and the current bearer token. If boot itself fails, check the host's cgroup configuration and the systemd-related options in `1stRun.sh`.

## References

- [cmdshellmcp project](https://github.com/ag88/cmdshellmcp)
- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Linux post-installation steps for Docker Engine](https://docs.docker.com/engine/install/linux-postinstall/)
- [Docker attach](https://docs.docker.com/reference/cli/docker/container/attach/)
- [Docker exec](https://docs.docker.com/reference/cli/docker/container/exec/)
- [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)
