"""hard_rules.py - 硬规则 R1-R6（prompt 改写与风险词预过滤）。

集中维护所有"硬约束"，与 agnes_common.preprocess_prompt 解耦。
本文件被测试时直接 import，不依赖 agnes_common。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


# ===== R1: 结构化 prompt 判定 =====
_STRUCTURED_PATTERNS = [
    re.compile(r"^\s*\{.*\}\s*$", re.DOTALL),  # JSON
    re.compile(r"\b\w+\s*:\s*[^\n,;{}]+", re.MULTILINE),  # 字段:值
    re.compile(r"\[VISUAL\]", re.IGNORECASE),
    re.compile(r"\[SPEECH\]", re.IGNORECASE),
    re.compile(r"\[ACTION\]", re.IGNORECASE),
    re.compile(r"\[CAMERA\]", re.IGNORECASE),
    re.compile(r"\[SHOT\s*\d", re.IGNORECASE),  # [SHOT 1]
    re.compile(r"f/\d", re.IGNORECASE),  # 摄影参数
    re.compile(r"\bISO\s*\d", re.IGNORECASE),  # ISO 100
    re.compile(r"\b\d+mm\b", re.IGNORECASE),  # 35mm / 50mm
]


def is_structured_prompt(prompt: str) -> bool:
    """判定 prompt 是否为结构化输入（JSON / 字段值 / 镜头脚本 / 摄影参数块）。"""
    if not prompt:
        return False
    for pat in _STRUCTURED_PATTERNS:
        if pat.search(prompt):
            return True
    return False


# ===== R2: 人物默认中日韩（东亚）=====
_HUMAN_TOKENS = [
    "woman", "man", "girl", "boy", "lady", "gentleman", "model", "character",
    "人物", "女人", "男人", "女孩", "男孩", "女子", "男士", "女士", "人物",
    "美少女", "少年", "少女", "帅哥", "美女",
]

# 已经显式指定国家/人种 → 不需要补全
_NATIONALITY_TOKENS = [
    "chinese", "japanese", "korean", "asian", "east asian",
    "american", "european", "african", "latina", "hispanic", "indian", "middle eastern",
    "中国", "日本", "韩国", "亚洲", "东亚",
    "美国", "欧洲", "非洲", "拉美", "印度", "中东", "法国", "英国", "意大利", "俄罗斯",
]

_NATIONALITY_DEFAULT = "East Asian"  # 默认：东亚


def _has_human_subject(prompt: str) -> bool:
    p = prompt.lower()
    return any(tok in p for tok in _HUMAN_TOKENS)


def _has_nationality(prompt: str) -> bool:
    p = prompt.lower()
    return any(tok in p for tok in _NATIONALITY_TOKENS)


def infer_nationality(prompt: str) -> Optional[str]:
    """推断人物的国家/人种。None 表示无人物；字符串表示已推断的默认族裔。"""
    if not _has_human_subject(prompt):
        return None
    if _has_nationality(prompt):
        return "explicit"
    return _NATIONALITY_DEFAULT


# ===== R3 / R4: 敏感词保留（不替换）=====
# 这些词在 R3/R4 中明确"必须保留"，不要主动降级。
# 任何"自动审查"逻辑都不应该修改这些词。
PROTECTED_TOKENS = [
    "voluptuous",
    "cleavage",
    "busty",  # 与 voluptuous 同类
    "sultry",
    "seductive",
]


def has_protected_token(prompt: str) -> bool:
    p = prompt.lower()
    return any(tok in p for tok in PROTECTED_TOKENS)


# ===== R5: 风险词检测（v3.5+ P0#23）=====
# 服务端内容审核容易拒绝的词；检测到后建议改写
_RISKY_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # (pattern, category, friendly_alternative)
    (re.compile(r"\b(plasma\s+sword|laser\s+sword)\b", re.IGNORECASE),
     "energy_weapon", "glowing energy blade"),
    (re.compile(r"\b(samurai|katana|shogun)\b", re.IGNORECASE),
     "warrior", "traditional warrior with curved blade"),
    (re.compile(r"\b(chrome\s+armor|mech\s+suit|power\s+armor)\b", re.IGNORECASE),
     "armor", "futuristic armored suit"),
    (re.compile(r"\b(blood|bloody|gore|severed)\b", re.IGNORECASE),
     "violence", "intense battle scene"),
    (re.compile(r"\b(nude|nudity|naked|topless)\b", re.IGNORECASE),
     "nsfw", "figure in elegant attire"),
    (re.compile(r"\b(gun|firearm|rifle|pistol)\b", re.IGNORECASE),
     "weapon", "futuristic sidearm"),
    (re.compile(r"\b(skull|skeleton|undead|zombie)\b", re.IGNORECASE),
     "horror", "dark ominous figure"),
    (re.compile(r"\b(explosion|detonate|bomb)\b", re.IGNORECASE),
     "destruction", "dramatic energy burst"),
    (re.compile(r"\b(war|battlefield|massacre)\b", re.IGNORECASE),
     "conflict", "epic confrontation"),
]


def find_risky_patterns(prompt: str) -> List[Dict[str, str]]:
    """扫描 prompt 中的风险词；返回 [{category, alternative, matched}, ...]。"""
    findings = []
    for pat, cat, alt in _RISKY_PATTERNS:
        m = pat.search(prompt)
        if m:
            findings.append({
                "category": cat,
                "alternative": alt,
                "matched": m.group(0),
            })
    return findings


def rewrite_risky_prompt(prompt: str) -> Tuple[str, List[Dict[str, str]]]:
    """将风险词替换为友好替代；返回 (rewritten, findings)。"""
    findings = find_risky_patterns(prompt)
    if not findings:
        return prompt, []
    rewritten = prompt
    for f in findings:
        # 简单大小写不敏感的替换（保留原 case 边界）
        rewritten = re.sub(
            re.escape(f["matched"]),
            f["alternative"],
            rewritten,
            flags=re.IGNORECASE,
        )
    return rewritten, findings


# ===== 统一入口：apply_hard_rules =====
def apply_hard_rules(prompt: str, *, translate_zh_to_en: bool = False) -> Dict:
    """应用 R1-R6 全部硬规则。

    返回：
      {
        "prompt":  最终 prompt,
        "original":  原始 prompt,
        "r_action":  "pass" | "rewrite" | "filter" | "pass_structured",
        "r_rules":   ["R1", "R2", ...]   # 命中的规则编号
        "r_inferred_nationality":  None | "East Asian" | "explicit"
        "aspect_ratio":  None | "16:9" | "2:3" | "3:2" | ...
        "r_findings":    [...]   # R5 命中的风险词详情
      }
    """
    out = {
        "prompt": prompt,
        "original": prompt,
        "r_action": "pass",
        "r_rules": [],
        "r_inferred_nationality": None,
        "aspect_ratio": None,
        "r_findings": [],
    }

    # 提取 aspect_ratio（R1 子规则，但仅在结构化 prompt 中处理）
    ar_match = re.search(r"aspect_ratio\s*[=:]\s*[\"']?(\d+\s*:\s*\d+)[\"']?", prompt, re.IGNORECASE)
    if ar_match:
        out["aspect_ratio"] = ar_match.group(1).replace(" ", "")

    # R1: 结构化 prompt 透传
    if is_structured_prompt(prompt):
        out["r_action"] = "pass_structured"
        out["r_rules"].append("R1")
        out["r_inferred_nationality"] = infer_nationality(prompt)
        # R3/R4 仍然需要标记（结构化 prompt 中含敏感词时也保留）
        if has_protected_token(prompt):
            out["r_rules"].append("R3")
            out["r_rules"].append("R4")
        return out

    # R5: 风险词预过滤（在改写之前）
    rewritten, findings = rewrite_risky_prompt(prompt)
    if findings:
        out["r_findings"] = findings
        out["r_rules"].append("R5")
        # 不直接替换，只在改写阶段使用
        # 如果 prompt 已被改写，更新并标记
        if rewritten != prompt:
            out["prompt"] = rewritten

    # R2: 人物默认中日韩
    nat = infer_nationality(out["prompt"])
    out["r_inferred_nationality"] = nat
    if nat and nat != "explicit" and nat == _NATIONALITY_DEFAULT:
        out["r_rules"].append("R2")
        # 注入"East Asian"到 prompt 头部
        if "east asian" not in out["prompt"].lower() and "asian" not in out["prompt"].lower():
            out["prompt"] = f"East Asian {out['prompt']}"

    # R3 / R4: 敏感词保留（不替换，但标记）
    if has_protected_token(out["prompt"]):
        out["r_rules"].append("R3")
        out["r_rules"].append("R4")

    # 动作标记
    if out["prompt"] != prompt:
        out["r_action"] = "rewrite"
    elif out["r_rules"]:
        out["r_action"] = "filter"

    return out
