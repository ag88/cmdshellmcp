#!/usr/bin/bash
IMAGE_NAME="cmdshellmcp-image"
CONTAINER_NAME="cmdshellmcp"
# To map cmdshellmcp port 8003 to localhost use
#    --publish 127.0.0.1:8003:8003 \
# To map cmdshellmcp port 8003 to all interfaces on the current host use
#    --publish 8003:8003 \
# Note that this script currently publish an additional port 5000 as a spare port
# you can use this port from within the container for any purpose
# e.g. if the LLM generates a python flask app that needs a port this port can be
# used
#    --publish 5000:5000 
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
    --publish 5000:5000 \
    $IMAGE_NAME
