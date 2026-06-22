"""prompt_translator.py - 中文 → 英文翻译（中文 prompt 预处理）。

P1.6 改进落地：Agnes 视频 API 对中文支持弱（实测 0% 成功率），建议开启翻译。
"""
from __future__ import annotations

import json
import os
import re
import time
from hashlib import sha256
from pathlib import Path
from typing import Optional

# 内置中文 → 英文 stub 词典（覆盖常见角色 / 场景 / 风格）
STUB_ZH_EN = {
    # 人物 / 角色
    "美少女": "young woman",
    "少女": "young woman",
    "女孩": "girl",
    "少年": "boy",
    "男人": "man",
    "女人": "woman",
    "美女": "beautiful woman",
    "帅哥": "handsome man",
    "人物": "character",
    "小孩": "child",
    "婴儿": "baby",
    "老人": "elderly person",
    # 服饰
    "旗袍": "qipao (Chinese cheongsam dress)",
    "汉服": "hanfu (traditional Chinese clothing)",
    "和服": "kimono (Japanese traditional dress)",
    "韩服": "hanbok (Korean traditional dress)",
    "西装": "suit",
    "礼服": "evening gown",
    "校服": "school uniform",
    "婚纱": "wedding dress",
    "铠甲": "armor",
    # 体型 / 特征
    "微乳": "petite bust",
    "巨乳": "voluptuous bust",
    "长发": "long hair",
    "短发": "short hair",
    "黑发": "black hair",
    "金发": "blonde hair",
    "红发": "red hair",
    "蓝发": "blue hair",
    "白发": "white hair",
    # 风格
    "写实": "photorealistic",
    "写实风格": "photorealistic style",
    "水彩": "watercolor",
    "油画": "oil painting",
    "插画": "illustration",
    "动漫": "anime",
    "二次元": "anime",
    "赛博朋克": "cyberpunk",
    "国风": "Chinese traditional style",
    "中国风": "Chinese style",
    "日式": "Japanese style",
    "韩式": "Korean style",
    "古风": "ancient Chinese style",
    # 场景
    "森林": "forest",
    "海边": "seaside",
    "山": "mountain",
    "城市": "city",
    "街道": "street",
    "咖啡馆": "cafe",
    "咖啡厅": "café",
    "宫殿": "palace",
    "寺庙": "temple",
    "夜景": "night scene",
    "黄昏": "sunset",
    "清晨": "early morning",
    "雪景": "snowy scene",
    "夜景": "night scene",
    "花园": "garden",
    # 镜头 / 动作
    "特写": "close-up",
    "远景": "wide shot",
    "中景": "medium shot",
    "侧身": "side view",
    "背影": "back view",
    "微笑": "smiling",
    "奔跑": "running",
    "跳舞": "dancing",
    # 通用修饰
    "美丽的": "beautiful",
    "可爱的": "cute",
    "优雅的": "elegant",
    "神秘的": "mysterious",
    "绚丽的": "gorgeous",
    "高清": "high definition",
    "超清": "ultra high definition",
    "细节": "intricate detail",
}

# 匹配中文字符
_RE_ZH = re.compile(r"[\u4e00-\u9fff]")


def has_chinese(text: str, threshold: int = 1) -> bool:
    """检测文本中是否含中文（默认 1 个字符即可触发）。"""
    if not text:
        return False
    return len(_RE_ZH.findall(text)) >= threshold


def zh_ratio(text: str) -> float:
    """中文占比。"""
    if not text:
        return 0.0
    zh = len(_RE_ZH.findall(text))
    return zh / max(len(text), 1)


def stub_translate(text: str) -> str:
    """使用内置词典翻译中文 → 英文（逐词替换）。"""
    out = text
    # 长词优先匹配（避免短词覆盖长词）
    for zh in sorted(STUB_ZH_EN.keys(), key=len, reverse=True):
        if zh in out:
            out = out.replace(zh, STUB_ZH_EN[zh])
    return out


def google_translate(text: str, api_key: str) -> str:
    """调用 Google Cloud Translate v2 API（需 API Key）。"""
    import requests

    if not api_key:
        raise ValueError("GOOGLE_TRANSLATE_API_KEY 未配置")
    url = "https://translation.googleapis.com/language/translate/v2"
    params = {"key": api_key, "q": text, "target": "en", "format": "text"}
    resp = requests.post(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["data"]["translations"][0]["translatedText"]


def openai_translate(text: str, api_key: str, model: str = "gpt-4o-mini") -> str:
    """调用 OpenAI Chat Completions 做翻译。"""
    import requests

    if not api_key:
        raise ValueError("OPENAI_API_KEY 未配置")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a translator. Translate the user input to natural, vivid English suitable for image/video generation prompts. Only output the translated text, no explanations."},
            {"role": "user", "content": text},
        ],
        "temperature": 0.4,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def translate_prompt(
    text: str,
    provider: str = "stub",
    google_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    openai_model: str = "gpt-4o-mini",
    cache_path: Optional[Path] = None,
) -> str:
    """根据 provider 调用对应翻译函数；可选缓存。"""
    if not has_chinese(text):
        return text

    # 缓存 key（SHA256）
    cache_key = None
    cache = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    cache_key = sha256(f"{provider}:{text}".encode("utf-8")).hexdigest()
    if cache_key in cache:
        return cache[cache_key]

    # 翻译
    if provider == "stub":
        result = stub_translate(text)
    elif provider == "google":
        result = google_translate(text, google_api_key or "")
    elif provider == "openai":
        result = openai_translate(text, openai_api_key or "", openai_model)
    else:
        raise ValueError(f"unknown provider: {provider}")

    # 写缓存
    if cache_path:
        cache[cache_key] = result
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="中文 → 英文 prompt 翻译器")
    parser.add_argument("text", nargs="?", help="要翻译的文本")
    parser.add_argument("--provider", default="stub", choices=["stub", "google", "openai"], help="翻译 provider")
    parser.add_argument("--cache", default=None, help="缓存文件路径")
    parser.add_argument("--ratio", action="store_true", help="仅显示中文占比")
    args = parser.parse_args()

    if args.ratio:
        if not args.text:
            print("0.0")
        else:
            print(f"{zh_ratio(args.text):.2%}")
        return 0

    if not args.text:
        parser.print_help()
        return 1

    cache_path = Path(args.cache) if args.cache else None
    result = translate_prompt(
        args.text,
        provider=args.provider,
        cache_path=cache_path,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
