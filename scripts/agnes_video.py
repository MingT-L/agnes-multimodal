"""agnes_video.py - Agnes 文生视频 CLI（单条调用，异步轮询）。

Usage:
    python agnes_video.py "Cyberpunk city at night" --duration 5 --width 1152 --height 768
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agnes_common import (
    AgnesClient,
    AgnesConfig,
    AgnesAPIError,
    get_logger,
    load_config,
    preprocess_prompt,
)


# num_frames 上限表（v3.5+ 实测）
# WxH（长 * 宽） → num_frames 最大允许值
NUM_FRAMES_LIMIT = {
    (1152, 768): 409,   # 横屏 3:2
    (768, 1152): 409,   # 竖屏 2:3
    (1088, 832): 441,   # 横屏 13:10
    (832, 1088): 441,   # 竖屏 10:13
    (832, 480): 441,    # SD 横屏
    (480, 832): 441,    # SD 竖屏
}
DEFAULT_NUM_FRAMES_LIMIT = 409  # 未列出分辨率的保守默认值


def calc_num_frames(duration: int, frame_rate: int = 24) -> int:
    """根据时长计算 num_frames（满足 8n+1）。"""
    raw = duration * frame_rate
    # 调整为 8n+1
    n = (raw - 1) // 8
    return max(8 * n + 1, 9)  # 最少 9 帧


def get_num_frames_limit(width: int, height: int) -> int:
    return NUM_FRAMES_LIMIT.get((width, height), DEFAULT_NUM_FRAMES_LIMIT)


def create_video_task(
    client: AgnesClient,
    prompt: str,
    width: int = 1152,
    height: int = 768,
    num_frames: int = 121,
    duration: int | None = None,
    frame_rate: int = 24,
) -> dict:
    """创建视频生成任务。"""
    if duration is not None and num_frames is None:
        num_frames = calc_num_frames(duration, frame_rate)

    # 预校验
    limit = get_num_frames_limit(width, height)
    if num_frames > limit:
        raise AgnesAPIError(
            f"num_frames={num_frames} 超过该分辨率上限 {limit}（{width}x{height}）",
            status_code=400,
            error_type="param_invalid",
        )

    payload = {
        "model": "agnes-video-v2.0",
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_frames": num_frames,
    }
    return client.post("/videos", json_body=payload)


def poll_video_task(
    client: AgnesClient,
    task_id: str,
    max_wait: int = 600,
    interval: int = 5,
) -> dict:
    """轮询任务状态。"""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = client.get(f"/videos/{task_id}")
        except AgnesAPIError as e:
            if e.status_code and 400 <= e.status_code < 500:
                raise
            time.sleep(interval)
            continue

        status = r.get("status", "unknown")
        if status in ("succeeded", "success", "completed"):
            return r
        if status in ("failed", "cancelled"):
            raise AgnesAPIError(
                f"视频任务失败：{r.get('error', r)}",
                status_code=400,
                error_type="content_policy",
            )
        time.sleep(interval)
    raise AgnesAPIError(
        f"视频任务超时（> {max_wait}s）",
        status_code=504,
        error_type="timeout",
    )


def download_video(url: str, dest: Path, timeout: int = 600) -> Path:
    """下载视频到本地。"""
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
    parser = argparse.ArgumentParser(description="Agnes 文生视频 CLI（异步轮询）")
    parser.add_argument("prompt", nargs="?", help="视频描述 prompt")
    parser.add_argument("--prompt-file", default=None, help="从文件读取 prompt（适合多行 / 长 prompt）")
    parser.add_argument("--duration", type=int, default=5, help="目标时长（秒）")
    parser.add_argument("--width", type=int, default=1152, help="视频宽度")
    parser.add_argument("--height", type=int, default=768, help="视频高度")
    parser.add_argument("--num-frames", type=int, default=None, help="帧数（不指定则按 duration 算）")
    parser.add_argument("--frame-rate", type=int, default=24, help="帧率")
    parser.add_argument("--max-wait", type=int, default=600, help="最大等待秒数")
    parser.add_argument("--out-dir", default=None, help="输出目录")
    parser.add_argument("--no-download", action="store_true", help="不下载到本地")
    parser.add_argument("--no-translate", action="store_true", help="禁用中文翻译")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

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

    logger = get_logger("agnes_video")
    client = AgnesClient(cfg, logger)

    # prompt 预处理
    prompt, meta = preprocess_prompt(prompt, cfg)
    if not args.json and meta.get("r_action") not in (None, "pass"):
        logger.info(f"prompt 预处理：action={meta['r_action']} rules={meta['r_rules']}")

    # 1) 创建任务
    try:
        create_resp = create_video_task(
            client,
            prompt=prompt,
            width=args.width,
            height=args.height,
            num_frames=args.num_frames,
            duration=args.duration,
            frame_rate=args.frame_rate,
        )
    except AgnesAPIError as e:
        logger.error(f"创建任务失败：{e}")
        return 2

    task_id = create_resp.get("id") or create_resp.get("task_id")
    if not task_id:
        logger.error("创建任务响应无 id 字段")
        if args.json:
            print(json.dumps(create_resp, ensure_ascii=False, indent=2))
        return 3

    # 2) 轮询
    try:
        result = poll_video_task(client, task_id, max_wait=args.max_wait)
    except AgnesAPIError as e:
        logger.error(f"轮询失败：{e}")
        return 4

    # 3) 下载
    # 兼容多种字段：video_url / url / output.url
    video_url = (
        result.get("video_url")
        or result.get("url")
        or (result.get("output") or {}).get("url")
    )
    if not video_url:
        logger.error("轮询响应无 video_url 字段")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 5

    saved_path = video_url
    if not args.no_download:
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        ts = meta.get("timestamp", "")
        dest = cfg.output_dir / f"video_{task_id[:8]}_{ts}.mp4"
        try:
            download_video(video_url, dest)
            saved_path = str(dest)
        except Exception as e:
            logger.warning(f"下载失败：{e}（保留 URL）")

    out = {
        "prompt_original": meta.get("original"),
        "prompt_used": prompt,
        "r_action": meta.get("r_action"),
        "r_rules": meta.get("r_rules"),
        "task_id": task_id,
        "saved_path": saved_path,
        "raw": result,
    }

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(saved_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
