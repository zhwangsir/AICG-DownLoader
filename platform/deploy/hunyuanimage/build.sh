#!/bin/bash
# 构建 HunyuanImage 2.1 FP8 镜像
set -e
docker build -t hunyuanimage:2.1-fp8 .
