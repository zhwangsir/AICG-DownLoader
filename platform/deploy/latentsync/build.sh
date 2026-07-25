#!/bin/bash
# 构建 LatentSync 1.6 唇形同步服务镜像
set -e
cd "$(dirname "$0")"
docker build -t latentsync-1.6:latest .
