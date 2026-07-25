#!/bin/bash
# 构建 xDiT HunyuanVideo-I2V 服务镜像
set -e
cd "$(dirname "$0")"

IMAGE_NAME="xdit-hunyuanvideo:latest"

echo "==> 构建镜像 ${IMAGE_NAME} ..."
docker build -t "${IMAGE_NAME}" .

echo "==> 构建完成"
docker images "${IMAGE_NAME}"
