#!/usr/bin/bash
IMAGE_NAME="cmdshellmcp-image"
CONTAINER_NAME="cmdshellmcp"
# Configure host-side publishing without changing the server's listening address.
# Port 8003 serves MCP; optional port 5000 and additional ports reserve access
# for applications run inside the container, such as an LLM-generated Flask app.
PUBLISH_ARGS=()
HOST_PORTS=()
CONTAINER_PORTS=()
PORT_BINDINGS=()

cancel_input() {
    printf '\nPort configuration cancelled; container was not created.\n' >&2
    exit "$1"
}

trap 'cancel_input 130' INT
trap 'cancel_input 143' TERM

read_prompt() {
    printf '%s' "$1"
    if ! IFS= read -r REPLY; then
        cancel_input 1
    fi
}

valid_port() {
    local port="$1"
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    # Strip leading zeros before arithmetic, and bound length to avoid overflow.
    while [[ ${#port} -gt 1 && "$port" == 0* ]]; do
        port="${port#0}"
    done
    [[ ${#port} -le 5 ]] || return 1
    (( 10#$port >= 1 && 10#$port <= 65535 )) || return 1
    PORT_NUMBER=$((10#$port))
}

prompt_port() {
    local label="$1" default="$2"
    while true; do
        read_prompt "$label [$default]: "
        if valid_port "${REPLY:-$default}"; then
            return
        fi
        printf 'Enter an integer port from 1 to 65535.\n'
    done
}

prompt_yes_no() {
    local prompt="$1" default="$2"
    while true; do
        read_prompt "$prompt"
        case "${REPLY:-$default}" in
            y|Y|yes|YES|Yes) return 0 ;;
            n|N|no|NO|No) return 1 ;;
            *) printf 'Please answer yes or no.\n' ;;
        esac
    done
}

prompt_port_binding() {
    printf '\n%s\n' "$1"
    printf '  1) Localhost only (default)\n  2) All network interfaces\n\n'
    while true; do
        read_prompt 'Selection [1]: '
        case "${REPLY:-1}" in
            1) PORT_BINDING='127.0.0.1'; return ;;
            2) PORT_BINDING='0.0.0.0'; return ;;
            *) printf 'Please select 1 or 2.\n' ;;
        esac
    done
}

host_port_used() {
    local port
    for port in "${HOST_PORTS[@]}"; do
        [[ "$port" == "$1" ]] && return 0
    done
    return 1
}

add_port() {
    local host_port="$1" container_port="$2" binding="$3"
    # Both offered addresses overlap, so repeated host ports always conflict.
    if host_port_used "$host_port"; then
        printf 'Host port %s is already configured.\n' "$host_port" >&2
        return 1
    fi
    PUBLISH_ARGS+=(--publish "$binding:$host_port:$container_port")
    HOST_PORTS+=("$host_port")
    CONTAINER_PORTS+=("$container_port")
    PORT_BINDINGS+=("$binding")
}

advance_suggested_port() {
    local attempts port used
    for ((attempts = 0; attempts < 65535; attempts++)); do
        SUGGESTED_PORT=$((SUGGESTED_PORT % 65535 + 1))
        used=false
        for port in "${CONTAINER_PORTS[@]}" "${HOST_PORTS[@]}"; do
            if [[ "$port" == "$SUGGESTED_PORT" ]]; then
                used=true
                break
            fi
        done
        if [[ "$used" == false ]]; then
            return 0
        fi
    done
    printf 'No unused port remains for another suggestion.\n' >&2
    cancel_input 1
}

printf '=== Docker Port Configuration ===\n'
printf 'Localhost binding is safer and restricts ordinary direct access to the Docker host.\n'
printf 'All-interface binding exposes the port through the host network interfaces, subject to firewall rules.\n'

prompt_port_binding 'Port 8003 (cmdshellmcp MCP server)'
add_port 8003 8003 "$PORT_BINDING" || cancel_input 1

if prompt_yes_no 'Publish port 5000 for applications inside the container? [Y/n]: ' y; then
    prompt_port_binding 'Port 5000 (spare application port)'
    add_port 5000 5000 "$PORT_BINDING" || cancel_input 1
fi

SUGGESTED_PORT=5001
while prompt_yes_no 'Add another published port? [y/N]: ' n; do
    prompt_port 'Container port' "$SUGGESTED_PORT"
    container_port="$PORT_NUMBER"
    while true; do
        prompt_port 'Host port' "$container_port"
        host_port="$PORT_NUMBER"
        if ! host_port_used "$host_port"; then
            break
        fi
        printf 'Host port %s is already configured; choose another host port.\n' "$host_port"
    done
    prompt_port_binding "Port $container_port (additional application port)"
    add_port "$host_port" "$container_port" "$PORT_BINDING" || cancel_input 1
    SUGGESTED_PORT="$container_port"
    advance_suggested_port
done

printf '\n=== Docker Published Ports ===\n\n'
printf '%-22s %s\n' 'Host binding' 'Container port'
for ((i = 0; i < ${#HOST_PORTS[@]}; i++)); do
    printf '%-22s %s\n' "${PORT_BINDINGS[i]}:${HOST_PORTS[i]}" "${CONTAINER_PORTS[i]}"
done
printf '\n127.0.0.1 = localhost only\n0.0.0.0   = all network interfaces\n\n'
if ! prompt_yes_no 'Start container with these settings? [Y/n]: ' y; then
    printf 'Container was not created.\n'
    exit 0
fi

# Restore normal signal handling before attaching to Docker's interactive console.
trap - INT TERM
docker run -it \
    --name "$CONTAINER_NAME" \
    --hostname cmdshellmcp \
    --privileged \
    --cgroupns=host \
    --tmpfs /run \
    --tmpfs /run/lock \
    --tmpfs /tmp \
    --volume /sys/fs/cgroup:/sys/fs/cgroup:rw \
    "${PUBLISH_ARGS[@]}" \
    "$IMAGE_NAME"
