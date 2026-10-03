#!/usr/bin/bash
DOCKERFILE="Dockerfile_cmdshellmcp"
IMAGE_NAME="cmdshellmcp-image"
docker build -f $DOCKERFILE -t $IMAGE_NAME .
