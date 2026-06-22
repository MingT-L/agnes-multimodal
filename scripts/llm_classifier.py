"""llm_classifier.py - LLM 错误分类器（P2.11）。

使用 OpenAI 模型对错误信息进行精细分类，补充正则匹配的盲区。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, Optional

ERROR_CATEGORIES = [
    "client",         # 4xx，客户端错误（如 400 / 401 / 403 / 404）
    "rate_limit",     # 429 限流
    "server",         # 5xx，服务器错误
    "timeout",        # 网络超时
    "content_policy", # 内容审核
    "param_invalid",  # 参数错误（如 num_frames 越界）
    "unknown",        # 无法识别
]

CLASSIFY_SYSTEM_PROMPT = """You are an API error classifier. Given a raw error message, classify it into exactly one of the following categories:

- client: 4xx errors caused by client request (e.g., 400/401/403/404, invalid API key, bad request format)
- rate_limit: 429 / RPM / QPS / throttling / too many requests
- server: 5xx errors from server side (e.g., 500/502/503/504, internal error, service unavailable)
- timeout: connection / read / request timeout, network errors
- content_policy: content moderation rejection, NSFW filter, prompt blocked by safety
- param_invalid: specific parameter validation failure (e.g., num_frames out of range, invalid size)
- unknown: anything else

Reply with a JSON object: {"category": "<one of above>", "reason": "<one-sentence explanation>"}"""


def classify_with_openai(
    error_text: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Dict[str, str]:
    """调用 OpenAI 分类错误。"""
    import requests

    if not api_key:
        raise ValueError("OPENAI_API_KEY 未配置")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": f"Error: {error_text}\n\nClassify this error."},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    result = json.loads(content)
    # 校验
    if result.get("category") not in ERROR_CATEGORIES:
        result["category"] = "unknown"
    return result


def classify_with_regex(error_text: str) -> Dict[str, str]:
    """使用正则做 fallback 分类（无需 OpenAI）。"""
    import re

    text = error_text.lower()

    # 401 / 403
    if re.search(r"\b(401|403)\b|unauthor|forbidden|invalid api key|api_key", text):
        return {"category": "client", "reason": "HTTP 401/403 or invalid key"}

    # 429 / 限流
    if re.search(r"\b(429)\b|rate.?limit|too many requests|throttl|quota", text):
        return {"category": "rate_limit", "reason": "HTTP 429 / throttling"}

    # 4xx（400 / 404）
    if re.search(r"\b400\b|bad request|unsupported.?param|invalid.?param", text):
        # 进一步判断 content_policy
        if re.search(r"content|safety|moderation|nsfw|policy|敏感|违规|内容", text):
            return {"category": "content_policy", "reason": "content policy violation"}
        # 参数错误
        if re.search(r"num.?frames|aspect|resolution|size|range|param", text):
            return {"category": "param_invalid", "reason": "parameter out of range or invalid"}
        return {"category": "client", "reason": "HTTP 4xx / bad request"}

    # 5xx
    if re.search(r"\b(5\d\d)\b|internal.?error|service.?unavailable|bad.?gateway", text):
        return {"category": "server", "reason": "HTTP 5xx / server error"}

    # timeout
    if re.search(r"timeout|timed.?out|read.?timeout|connection.?reset|connection.?refused|network", text):
        return {"category": "timeout", "reason": "network / timeout"}

    # 内容审核
    if re.search(r"content|safety|moderation|nsfw|policy|敏感|违规|内容", text):
        return {"category": "content_policy", "reason": "content policy violation"}

    return {"category": "unknown", "reason": "unrecognized error pattern"}


def classify_error(
    error_text: str,
    use_llm: bool = False,
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> Dict[str, str]:
    """主入口：先 regex fallback，LLM 启用时再 refine。"""
    base = classify_with_regex(error_text)
    if not use_llm or not api_key:
        return base
    if base["category"] == "unknown":
        try:
            return classify_with_openai(error_text, api_key, model)
        except Exception as e:
            base["reason"] = f"{base['reason']} (LLM fallback failed: {e})"
            return base
    return base


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="错误分类器（regex / LLM）")
    parser.add_argument("error", nargs="?", help="错误文本")
    parser.add_argument("--use-llm", action="store_true", help="使用 LLM 精细分类")
    parser.add_argument("--api-key", default=None, help="OpenAI API Key（默认读 OPENAI_API_KEY）")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI 模型")
    parser.add_argument("--from-log", default=None, help="从日志文件读取错误（每行一条）")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")

    if args.from_log:
        from pathlib import Path
        path = Path(args.from_log)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            return 1
        results = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = classify_error(line, use_llm=args.use_llm, api_key=api_key, model=args.model)
            results.append({"error": line[:200], **r})
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if not args.error:
        parser.print_help()
        return 1

    result = classify_error(args.error, use_llm=args.use_llm, api_key=api_key, model=args.model)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
