"""batch_image.py - 批量生图脚本（v3.1，含 6 个共享参数 + 智能限流）。

Usage:
    python batch_image.py --file prompts.txt --n 2 --interval 30
    python batch_image.py --file prompts.txt --shuffle --seed 42 --early-stop 5
    python batch_image.py --file prompts.txt --dry-run
    python batch_image.py --file prompts.txt --retry-failed log_xxx.json
"""
from __future__ import annotations

import argparse
import json
import random
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
    classify_error,
)
from agnes_image import generate_image, download_image
from llm_classifier import classify_with_openai


# v3.3 状态机
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def preflight_check(client: AgnesClient) -> bool:
    """启动前 API 健康检查（轻量调用）。"""
    try:
        client.post("/images/generations", json_body={
            "model": "agnes-image-2.1-flash",
            "prompt": "healthcheck",
            "size": "1024x1024",
            "n": 1,
        })
        return True
    except AgnesAPIError as e:
        if e.status_code in (400, 401, 403):
            return False
        return True  # 5xx 可能是临时服务问题，允许继续


def load_prompts(path: Path, shuffle: bool, seed: int | None) -> list[str]:
    """从文件读取 prompt 列表（每行一条）。"""
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
    out_dir: Path,
    size: str,
    n: int,
    ref_image: str | None,
    logger,
) -> dict:
    """执行单条生图，返回状态机 dict。"""
    try:
        result = generate_image(
            client,
            prompt=prompt,
            size=size,
            n=n,
            ref_image=ref_image,
        )
    except AgnesAPIError as e:
        cls = classify_error(str(e), use_llm=cfg.use_llm_classify, api_key=cfg.openai_api_key)
        return {
            "status": STATUS_FAILED,
            "error": str(e),
            "error_type": cls.get("category", "unknown"),
        }

    data = result.get("data", [])
    if not data:
        return {"status": STATUS_FAILED, "error": "no data", "error_type": "server"}

    out_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []
    for i, item in enumerate(data):
        url = item.get("url")
        if not url:
            continue
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = out_dir / f"image_{i+1:02d}_{ts}.png"
        try:
            download_image(url, dest)
            saved_paths.append(str(dest))
        except Exception as e:
            return {
                "status": STATUS_PARTIAL,
                "image_url": url,
                "saved_paths": saved_paths,
                "error": f"download failed: {e}",
            }

    return {
        "status": STATUS_SUCCESS,
        "saved_paths": saved_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生图（智能限流）")
    parser.add_argument("--file", required=True, help="prompt 文件路径（每行一条）")
    parser.add_argument("--out-dir", default=None, help="输出目录")
    parser.add_argument("--size", default="1024x1024", help="图片尺寸")
    parser.add_argument("--n", type=int, default=1, help="每个 prompt 生成张数")
    parser.add_argument("--interval", type=int, default=30, help="每条之间间隔（秒）")
    parser.add_argument("--ref-image", default=None, help="参考图 URL（全局）")
    parser.add_argument("--max-retries", type=int, default=2, help="每条最大重试次数")
    # v3.1 新增
    parser.add_argument("--shuffle", action="store_true", help="随机打乱 prompt 顺序")
    parser.add_argument("--seed", type=int, default=None, help="随机种子（与 --shuffle 配合）")
    parser.add_argument("--early-stop", type=int, default=0, help="连续 N 个失败后自动停止")
    parser.add_argument("--exclude-errors", default="", help="跳过指定错误类型（逗号分隔）")
    parser.add_argument("--no-preflight", action="store_true", help="跳过启动前 API 健康检查")
    parser.add_argument("--dry-run", action="store_true", help="干跑：只翻译 + 校验，不调 API")
    parser.add_argument("--retry-failed", default=None, help="从指定日志文件重试 failed 状态")
    parser.add_argument("--max-items", type=int, default=0, help="限制本次最多处理条数（0=不限）")
    args = parser.parse_args()

    cfg = load_config()
    if args.out_dir:
        cfg.output_dir = Path(args.out_dir)
    logger = get_logger("batch_image")
    client = AgnesClient(cfg, logger)

    # 1) 读取 prompts
    if args.retry_failed:
        # 从日志重试
        log_path = Path(args.retry_failed)
        log = json.loads(log_path.read_text(encoding="utf-8"))
        items = [
            (k, v.get("prompt_original", v.get("prompt", "")))
            for k, v in log.items()
            if v.get("status") == STATUS_FAILED
        ]
    else:
        prompts_path = Path(args.file)
        prompts = load_prompts(prompts_path, args.shuffle, args.seed)
        items = [(f"item_{i+1:03d}", p) for i, p in enumerate(prompts)]

    if args.max_items > 0:
        items = items[:args.max_items]
    logger.info(f"共 {len(items)} 条")

    # 2) 解析 --exclude-errors
    exclude_set = set(s.strip() for s in args.exclude_errors.split(",") if s.strip())

    # 3) 预校验（preflight）
    if not args.no_preflight and not args.dry_run:
        if not preflight_check(client):
            logger.error("API 健康检查失败，终止")
            return 1

    # 4) 逐条执行
    consecutive_failed = 0
    results = {}
    for idx, (key, raw_prompt) in enumerate(items, 1):
        # 跳过 excluded 错误
        if key in results and results[key].get("error_type") in exclude_set:
            results[key] = {
                "status": STATUS_SKIPPED,
                "skipped_reason": f"error_type={results[key].get('error_type')} in exclude list",
            }
            continue

        # 翻译
        prompt, meta = preprocess_prompt(raw_prompt, cfg)

        if args.dry_run:
            logger.info(f"[{idx}/{len(items)}] {key}: would generate '{prompt[:60]}...'")
            results[key] = {
                "status": "dry_run",
                "prompt_original": raw_prompt,
                "prompt_used": prompt,
                "r_action": meta.get("r_action"),
            }
            continue

        # 重试
        last_error = None
        for attempt in range(args.max_retries + 1):
            try:
                r = run_one(
                    client,
                    cfg,
                    prompt=prompt,
                    out_dir=cfg.output_dir / key,
                    size=args.size,
                    n=args.n,
                    ref_image=args.ref_image,
                    logger=logger,
                )
                r["prompt_original"] = raw_prompt
                r["prompt_used"] = prompt
                r["r_action"] = meta.get("r_action")
                r["r_rules"] = meta.get("r_rules")
                results[key] = r
                if r["status"] == STATUS_SUCCESS:
                    logger.info(f"[{idx}/{len(items)}] {key}: success")
                    break
                else:
                    last_error = r
                    if r["status"] in (STATUS_FAILED,) and r.get("error_type") in ("client", "param_invalid", "content_policy"):
                        # 不重试
                        break
            except Exception as e:
                last_error = {
                    "status": STATUS_FAILED,
                    "error": str(e),
                    "error_type": "unknown",
                }
            time.sleep(args.interval)
        else:
            # 重试耗尽
            if last_error:
                last_error["prompt_original"] = raw_prompt
                last_error["prompt_used"] = prompt
                results[key] = last_error

        # 限流 / 间隔
        if idx < len(items):
            time.sleep(args.interval)

        # 早停
        if results[key]["status"] in (STATUS_FAILED, STATUS_PARTIAL):
            consecutive_failed += 1
            if args.early_stop > 0 and consecutive_failed >= args.early_stop:
                logger.warning(f"连续 {consecutive_failed} 次失败，触发早停")
                # 后续标记 skipped
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
    log_path = cfg.output_dir / f"log_image_{ts}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"日志已写：{log_path}")

    # 汇总
    counts = {s: 0 for s in [STATUS_SUCCESS, STATUS_PARTIAL, STATUS_FAILED, STATUS_SKIPPED, "dry_run"]}
    for v in results.values():
        s = v.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if counts[STATUS_FAILED] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
