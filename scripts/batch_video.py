"""batch_video.py - 批量生视频脚本（v3.1，含 6 个共享 + 1 个视频专用）。

Usage:
    python batch_video.py --file prompts.txt --duration 5 --interval 30
    python batch_video.py --file prompts.txt --resume log_xxx.json --reuse-task
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from agnes_common import (
    AgnesClient,
    AgnesConfig,
    AgnesAPIError,
    get_logger,
    load_config,
    preprocess_prompt,
    classify_error,
)
from agnes_video import (
    create_video_task,
    poll_video_task,
    download_video,
    get_num_frames_limit,
    calc_num_frames,
)


STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def preflight_check(client: AgnesClient) -> bool:
    try:
        client.post("/videos", json_body={
            "model": "agnes-video-v2.0",
            "prompt": "healthcheck",
            "width": 1152,
            "height": 768,
            "num_frames": 9,
        })
        return True
    except AgnesAPIError as e:
        if e.status_code in (400, 401, 403):
            return False
        return True


def load_prompts(path: Path, shuffle: bool, seed: int | None) -> list[str]:
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    prompts = [l for l in lines if l and not l.startswith("#")]
    if shuffle:
        if seed is not None:
            random.seed(seed)
        random.shuffle(prompts)
    return prompts


def run_one(
    client: AgnesClient,
    cfg: AgnesConfig,
    prompt: str,
    width: int,
    height: int,
    num_frames: int,
    max_wait: int,
    out_dir: Path,
    logger,
) -> dict:
    """执行单条视频生成（带翻译）。"""
    # 预校验 num_frames
    limit = get_num_frames_limit(width, height)
    if num_frames > limit:
        return {
            "status": STATUS_FAILED,
            "error": f"num_frames={num_frames} 超过 {width}x{height} 上限 {limit}",
            "error_type": "param_invalid",
        }

    # 1) 创建
    try:
        create_resp = create_video_task(
            client,
            prompt=prompt,
            width=width,
            height=height,
            num_frames=num_frames,
        )
    except AgnesAPIError as e:
        cls = classify_error(str(e), use_llm=cfg.use_llm_classify, api_key=cfg.openai_api_key)
        return {
            "status": STATUS_FAILED,
            "error": str(e),
            "error_type": cls.get("category", "unknown"),
        }

    task_id = create_resp.get("id") or create_resp.get("task_id")
    if not task_id:
        return {"status": STATUS_FAILED, "error": "no task_id in create response", "error_type": "server"}

    # 2) 轮询
    try:
        result = poll_video_task(client, task_id, max_wait=max_wait)
    except AgnesAPIError as e:
        return {
            "status": STATUS_FAILED,
            "task_id": task_id,
            "error": str(e),
            "error_type": "timeout" if e.status_code == 504 else "unknown",
        }

    # 3) 下载
    video_url = (
        result.get("video_url")
        or result.get("url")
        or (result.get("output") or {}).get("url")
    )
    if not video_url:
        return {
            "status": STATUS_FAILED,
            "task_id": task_id,
            "error": "no video_url in poll response",
            "error_type": "server",
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"video_{task_id[:8]}.mp4"
    try:
        download_video(video_url, dest)
        return {
            "status": STATUS_SUCCESS,
            "task_id": task_id,
            "video_url": video_url,
            "saved_path": str(dest),
        }
    except Exception as e:
        return {
            "status": STATUS_PARTIAL,
            "task_id": task_id,
            "video_url": video_url,
            "error": f"download failed: {e}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生视频（智能限流 + 状态轮询）")
    parser.add_argument("--file", required=True, help="prompt 文件路径")
    parser.add_argument("--out-dir", default=None, help="输出目录")
    parser.add_argument("--duration", type=int, default=5, help="目标时长（秒）")
    parser.add_argument("--width", type=int, default=1152)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--num-frames", type=int, default=None, help="帧数（不指定则按 duration 算）")
    parser.add_argument("--frame-rate", type=int, default=24)
    parser.add_argument("--interval", type=int, default=30, help="每条之间间隔（秒）")
    parser.add_argument("--max-wait", type=int, default=600, help="单条最大等待（秒）")
    parser.add_argument("--max-retries", type=int, default=1, help="最大重试次数")
    # v3.1 共享
    parser.add_argument("--shuffle", action="store_true", help="随机打乱 prompt 顺序")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--early-stop", type=int, default=0)
    parser.add_argument("--exclude-errors", default="")
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    # v3.1 视频专用
    parser.add_argument("--resume", default=None, help="从指定日志文件续跑")
    parser.add_argument("--reuse-task", action="store_true", help="续跑时复用历史 task_id（避免重复扣费）")
    parser.add_argument("--max-items", type=int, default=0)
    args = parser.parse_args()

    cfg = load_config()
    if args.out_dir:
        cfg.output_dir = Path(args.out_dir)
    logger = get_logger("batch_video")
    client = AgnesClient(cfg, logger)

    # 计算 num_frames
    if args.num_frames is None:
        num_frames = calc_num_frames(args.duration, args.frame_rate)
    else:
        num_frames = args.num_frames

    # 1) 读取 prompts
    if args.resume:
        log_path = Path(args.resume)
        log = json.loads(log_path.read_text(encoding="utf-8"))
        if args.reuse_task:
            # 复用历史 task_id
            items = [
                (k, v.get("prompt_original", v.get("prompt", "")), v.get("task_id"))
                for k, v in log.items()
                if v.get("status") in (STATUS_FAILED, STATUS_PARTIAL) and v.get("task_id")
            ]
        else:
            items = [
                (k, v.get("prompt_original", v.get("prompt", "")))
                for k, v in log.items()
                if v.get("status") in (STATUS_FAILED, STATUS_PARTIAL)
            ]
    else:
        prompts = load_prompts(Path(args.file), args.shuffle, args.seed)
        items = [(f"item_{i+1:03d}", p) for i, p in enumerate(prompts)]

    if args.max_items > 0:
        items = items[:args.max_items]
    logger.info(f"共 {len(items)} 条 (num_frames={num_frames}, {args.width}x{args.height})")

    # 2) exclude
    exclude_set = set(s.strip() for s in args.exclude_errors.split(",") if s.strip())

    # 3) preflight
    if not args.no_preflight and not args.dry_run:
        if not preflight_check(client):
            logger.error("API 健康检查失败，终止")
            return 1

    # 4) 执行
    consecutive_failed = 0
    results = {}
    for idx, item in enumerate(items, 1):
        if args.reuse_task:
            key, raw_prompt, prev_task_id = item
        else:
            key, raw_prompt = item
            prev_task_id = None

        # 翻译
        prompt, meta = preprocess_prompt(raw_prompt, cfg)

        if args.dry_run:
            logger.info(f"[{idx}/{len(items)}] {key}: would generate '{prompt[:60]}...' (num_frames={num_frames})")
            results[key] = {
                "status": "dry_run",
                "prompt_original": raw_prompt,
                "prompt_used": prompt,
                "r_action": meta.get("r_action"),
            }
            continue

        # 执行 / 重试
        last_error = None
        for attempt in range(args.max_retries + 1):
            r = run_one(
                client,
                cfg,
                prompt=prompt,
                width=args.width,
                height=args.height,
                num_frames=num_frames,
                max_wait=args.max_wait,
                out_dir=cfg.output_dir / key,
                logger=logger,
            )
            r["prompt_original"] = raw_prompt
            r["prompt_used"] = prompt
            r["r_action"] = meta.get("r_action")
            results[key] = r
            if r["status"] == STATUS_SUCCESS:
                logger.info(f"[{idx}/{len(items)}] {key}: success")
                break
            last_error = r
            if r["status"] == STATUS_FAILED and r.get("error_type") in ("client", "param_invalid", "content_policy"):
                break
            time.sleep(args.interval)
        else:
            if last_error:
                last_error["prompt_original"] = raw_prompt
                last_error["prompt_used"] = prompt
                results[key] = last_error

        if idx < len(items):
            time.sleep(args.interval)

        if results[key]["status"] in (STATUS_FAILED, STATUS_PARTIAL):
            consecutive_failed += 1
            if args.early_stop > 0 and consecutive_failed >= args.early_stop:
                logger.warning(f"连续 {consecutive_failed} 次失败，触发早停")
                for k2, _ in items[idx:]:
                    if k2 not in results:
                        results[k2] = {
                            "status": STATUS_SKIPPED,
                            "skipped_reason": "early_stop triggered",
                        }
                break
        else:
            consecutive_failed = 0

    # 5) 写日志
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = cfg.output_dir / f"log_video_{ts}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"日志已写：{log_path}")

    counts = {s: 0 for s in [STATUS_SUCCESS, STATUS_PARTIAL, STATUS_FAILED, STATUS_SKIPPED, "dry_run"]}
    for v in results.values():
        s = v.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if counts[STATUS_FAILED] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
