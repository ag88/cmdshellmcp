#!/usr/bin/bash
IMAGE_NAME="cmdshellmcp-image"
CONTAINER_NAME="cmdshellmcp"
# To map cmdshellmcp port 8003 to localhost use
#    --publish 127.0.0.1:8003:8003 \
# To map cmdshellmcp port 8003 to all interfaces on the current host use
#    --publish 8003:8003 \
docker run -it \
    --name $CONTAINER_NAME \
    --hostname cmdshellmcp \
    --privileged \
    --cgroupns=host \
    --tmpfs /run \
    --tmpfs /run/lock \
    --tmpfs /tmp \
    --volume /sys/fs/cgroup:/sys/fs/cgroup:rw \
    --publish 8003:8003 \
    $IMAGE_NAME
