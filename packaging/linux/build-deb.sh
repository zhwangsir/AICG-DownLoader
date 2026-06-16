#!/usr/bin/env bash
# ============================================================================
#  build-deb.sh —— 用 dpkg-deb 把已编译好的二进制打成 .deb
#
#  约定：在仓库根目录调用，且 cargo build --release 已产出
#  target/release/comfy-downloader。release.yml 的 Linux job 会调用本脚本。
#
#  用法：
#      packaging/linux/build-deb.sh <version> <output-deb-path>
#  例：
#      packaging/linux/build-deb.sh 0.1.0 dist/comfy-downloader-v0.1.0-linux-amd64.deb
#
#  产物布局（标准 Debian / FHS）：
#      /usr/bin/comfy-downloader
#      /usr/share/applications/comfy-downloader.desktop
# ============================================================================
set -euo pipefail

VERSION="${1:?usage: build-deb.sh <version> <output-deb-path>}"
OUTPUT="${2:?usage: build-deb.sh <version> <output-deb-path>}"

BIN_NAME="comfy-downloader"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN_PATH="${REPO_ROOT}/target/release/${BIN_NAME}"
CONTROL_TEMPLATE="${REPO_ROOT}/packaging/linux/debian/control"
DESKTOP_FILE="${REPO_ROOT}/packaging/linux/${BIN_NAME}.desktop"

[ -f "$BIN_PATH" ] || { echo "error: 二进制不存在：$BIN_PATH（先 cargo build --release）" >&2; exit 1; }

# 临时打包根目录
PKGDIR="$(mktemp -d)"
trap 'rm -rf "$PKGDIR"' EXIT

# DEBIAN 控制信息：从模板渲染版本号
mkdir -p "$PKGDIR/DEBIAN"
sed "s/__VERSION__/${VERSION}/g" "$CONTROL_TEMPLATE" > "$PKGDIR/DEBIAN/control"

# 二进制 -> /usr/bin
install -Dm0755 "$BIN_PATH" "$PKGDIR/usr/bin/${BIN_NAME}"

# 桌面项 -> /usr/share/applications
install -Dm0644 "$DESKTOP_FILE" "$PKGDIR/usr/share/applications/${BIN_NAME}.desktop"

# 许可证（Debian 习惯放 /usr/share/doc/<pkg>/copyright，可选但合规）
if [ -f "${REPO_ROOT}/LICENSE" ]; then
  install -Dm0644 "${REPO_ROOT}/LICENSE" "$PKGDIR/usr/share/doc/${BIN_NAME}/copyright"
fi

mkdir -p "$(dirname "$OUTPUT")"
dpkg-deb --build --root-owner-group "$PKGDIR" "$OUTPUT"

echo "built: $OUTPUT"
