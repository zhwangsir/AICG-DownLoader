#!/usr/bin/env python3
"""批量下载 RAG 风格库配套的 Civitai Flux.1 D LoRA。

用法:
    cd platform/backend
    python scripts/download_loras.py

环境变量:
    CIVITAI_TOKEN   Civitai API Token（优先）
    LORA_DEST       覆盖目标目录
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

MANIFEST = Path(__file__).with_name("lora_manifest.json")
CONFIG_PATH = Path.home() / "Library/Application Support/comfy-downloader/config.json"


def load_token(cli_token: str | None) -> str:
    if cli_token:
        return cli_token
    if os.environ.get("CIVITAI_TOKEN"):
        return os.environ["CIVITAI_TOKEN"]
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            tok = cfg.get("civitai_token", "")
            if tok:
                return tok
        except Exception:
            pass
    raise RuntimeError(
        "未找到 CIVITAI_TOKEN：请传入 --token、设置环境变量 CIVITAI_TOKEN，"
        "或在 comfy-downloader/config.json 中配置 civitai_token"
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().upper()


def download_with_curl(url: str, part_path: Path, token: str) -> bool:
    cmd = [
        "curl",
        "-s",  # 静默，避免进度条刷屏
        "-L",  # 跟随重定向
        "-C", "-",  # 断点续传
        "--connect-timeout", "30",
        "--max-time", "1800",
        "--retry", "2",
        "--retry-delay", "5",
        "-H", f"Authorization: Bearer {token}",
        "-o", str(part_path),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    curl 失败 ({result.returncode}): {result.stderr.strip()[:200]}")
    return result.returncode == 0


def try_download(item: dict, dest_dir: Path, token: str, hosts: list[str]) -> bool:
    filename = item["filename"]
    final_path = dest_dir / filename
    part_path = dest_dir / (filename + ".part")
    expected_sha = item["sha256"].upper()

    if final_path.exists() and sha256_file(final_path) == expected_sha:
        print(f"[SKIP] {filename} 已存在且校验通过")
        return True

    # 如果旧文件校验失败，删除重来
    if final_path.exists():
        print(f"[WARN] {filename} 校验失败，删除重下")
        final_path.unlink()

    primary_url = item["download_url"]
    urls = [primary_url]
    parsed = urlparse(primary_url)
    for host in hosts:
        if host != parsed.netloc:
            alt = primary_url.replace(parsed.netloc, host, 1)
            urls.append(alt)

    for idx, url in enumerate(urls, start=1):
        host = urlparse(url).netloc
        print(f"[DOWN {idx}/{len(urls)}] {filename} <- {host}")
        # 切换源前清理残缺的 part 文件，避免 Range 错位
        if part_path.exists():
            part_path.unlink()
        if download_with_curl(url, part_path, token):
            print(f"[HASH] {filename} 校验中...")
            actual_sha = sha256_file(part_path)
            if actual_sha == expected_sha:
                shutil.move(str(part_path), str(final_path))
                print(f"[OK] {filename} 下载完成 ({actual_sha[:16]}...)")
                return True
            print(
                f"[HASH FAIL] {filename} SHA256 不匹配: "
                f"{actual_sha[:16]}... != {expected_sha[:16]}..."
            )
            part_path.unlink(missing_ok=True)
        time.sleep(1)

    print(f"[FAIL] {filename} 所有源均失败")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 RAG 风格 LoRA")
    parser.add_argument("--token", help="Civitai API Token")
    parser.add_argument("--dest", help="覆盖目标目录")
    parser.add_argument("--manifest", default=str(MANIFEST), help="清单文件路径")
    args = parser.parse_args()

    token = load_token(args.token)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    dest_dir = Path(args.dest) if args.dest else Path(manifest["destination_dir"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    hosts = manifest.get("hosts", ["civitai.com", "civitai.red"])

    print(f"目标目录: {dest_dir}")
    print(f"待下载: {len(manifest['items'])} 个 LoRA")
    print(f"备用源: {hosts}")
    print("-" * 60)

    ok = 0
    failed = 0
    skipped = 0
    start = time.time()

    for item in manifest["items"]:
        exists_correct = False
        final_path = dest_dir / item["filename"]
        if final_path.exists() and sha256_file(final_path) == item["sha256"].upper():
            print(f"[SKIP] {item['filename']} 已存在且校验通过")
            skipped += 1
            continue

        success = try_download(item, dest_dir, token, hosts)
        if success:
            ok += 1
        else:
            failed += 1

    elapsed = time.time() - start
    print("-" * 60)
    print(f"完成: 成功 {ok} / 跳过 {skipped} / 失败 {failed}")
    print(f"耗时: {elapsed:.1f}s")

    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
