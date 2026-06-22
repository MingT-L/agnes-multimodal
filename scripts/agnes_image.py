"""agnes_image.py - Agnes 文生图 / 图生图 CLI（单条调用）。

Usage:
    python agnes_image.py "A cat under cherry blossom" --size 1024x1024
    python agnes_image.py "改成水彩风格" --ref-image https://.../cat.png --size 1024x1024
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agnes_common import (
    AgnesClient,
    AgnesConfig,
    AgnesAPIError,
    get_logger,
    load_config,
    preprocess_prompt,
)


def generate_image(
    client: AgnesClient,
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
    ref_image: str | None = None,
    extra_body: dict | None = None,
) -> dict:
    """调用 Agnes 文生图 / 图生图 API。"""
    model = "agnes-image-2.0-flash" if ref_image else "agnes-image-2.1-flash"
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": n,
    }
    # 图生图需要 reference
    if ref_image:
        payload["extra_body"] = {"image": ref_image}
    if extra_body:
        payload.setdefault("extra_body", {}).update(extra_body)

    return client.post("/images/generations", json_body=payload)


def download_image(url: str, dest: Path, timeout: int = 300) -> Path:
    """下载图片到本地。"""
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    with dest.open("wb") as f:
        for chunk in resp.iter_content(64 * 1024):
            if chunk:
                f.write(chunk)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Agnes 文生图 / 图生图 CLI")
    parser.add_argument("prompt", nargs="?", help="图片描述 prompt")
    parser.add_argument("--prompt-file", default=None, help="从文件读取 prompt（适合多行 / 长 prompt）")
    parser.add_argument("--size", default="1024x1024", help="图片尺寸，如 1024x1024 / 1152x768")
    parser.add_argument("--n", type=int, default=1, help="生成数量（1-4）")
    parser.add_argument("--ref-image", default=None, help="参考图 URL（图生图模式）")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认 assets/outputs）")
    parser.add_argument("--no-download", action="store_true", help="不下载到本地")
    parser.add_argument("--no-translate", action="store_true", help="禁用中文翻译")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    # 读取 prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    elif args.prompt:
        prompt = args.prompt
    else:
        parser.print_help()
        return 1

    cfg = load_config()
    if args.out_dir:
        cfg.output_dir = Path(args.out_dir)
    if args.no_translate:
        cfg.translate_enabled = False

    logger = get_logger("agnes_image")
    client = AgnesClient(cfg, logger)

    # prompt 预处理
    prompt, meta = preprocess_prompt(prompt, cfg)
    if not args.json and meta.get("r_action") not in (None, "pass"):
        logger.info(f"prompt 预处理：action={meta['r_action']} rules={meta['r_rules']}")

    try:
        result = generate_image(
            client,
            prompt=prompt,
            size=args.size,
            n=args.n,
            ref_image=args.ref_image,
        )
    except AgnesAPIError as e:
        logger.error(f"API 错误：{e}")
        return 2

    # 解析结果
    data = result.get("data", [])
    if not data:
        logger.error("API 响应无 data 字段")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    saved_paths = []
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    for i, item in enumerate(data):
        url = item.get("url")
        if not url:
            continue
        if args.no_download:
            saved_paths.append(url)
            continue
        # 下载到本地
        ts = meta.get("timestamp", "")
        idx = i + 1
        ext = ".png"
        dest = cfg.output_dir / f"image_{idx:02d}_{ts}{ext}"
        try:
            download_image(url, dest)
            saved_paths.append(str(dest))
        except Exception as e:
            logger.warning(f"下载失败：{e}")
            saved_paths.append(url)  # 退回 URL

    out = {
        "prompt_original": meta.get("original"),
        "prompt_used": prompt,
        "r_action": meta.get("r_action"),
        "r_rules": meta.get("r_rules"),
        "saved_paths": saved_paths,
        "raw": result,
    }

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for p in saved_paths:
            print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
