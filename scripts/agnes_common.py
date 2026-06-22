"""
agnes_common.py
================
Agnes 多模态 API 公共模块：配置加载、HTTP 客户端、错误处理、日志、prompt 预处理。

设计原则：
  - 不引入重依赖（仅 python-dotenv + requests）
  - .env 查找顺序：环境变量 > Skill 根 .env > 仓库根 .env > 用户家目录 .env
  - 所有上层脚本（agnes_image / agnes_video / batch_*）都依赖本模块
  - 统一错误：AgnesAPIError，附带 status_code、message、request_id
  - 错误分类：5 类（rate_limit / timeout / server / client / unknown）
  - 智能退避：根据错误类型与连续失败次数动态调整等待时间
  - prompt 预处理：检测中文 → 自动翻译为英文（P1.6 改进）

注：项目遵循 2 空格缩进规则（个人偏好），不符合 PEP 8 默认 4 空格。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
# v3.3 修复：hard_rules 改为 top-level import（消除函数内 import 隐式循环依赖）
from hard_rules import apply_hard_rules  # noqa: E402

# ===== 公共常量 =====
# 约定：模型名称写为常量，避免散落字符串
MODEL_T2I = "agnes-image-2.1-flash"  # 纯文生图
MODEL_I2I = "agnes-image-2.0-flash"  # 图生图 / 图片编辑
MODEL_T2V = "agnes-video-v2.0"  # 文生视频

# 端点路径（在 BASE_URL 后拼接）
EP_CHAT = "/chat/completions"
EP_IMAGES = "/images/generations"
EP_VIDEOS = "/videos"
EP_VIDEO_TASK = "/videos/{task_id}"  # GET 时使用

# 图片尺寸白名单（按文档常见值）
IMAGE_SIZES = ["1024x1024", "1024x768", "768x1024", "1152x768", "768x1152"]

# 视频帧数约束：≤441 且满足 8n+1
VIDEO_NUM_FRAMES_LIMIT = 441

# ===== 按分辨率的 num_frames 上限（实测 v3.5+）=====
# 服务端实际限制按视频比例分类，不是统一的 441
# 格式：{ (width, height): max_num_frames }
# 数据来源：v3.5+ 实测（1152x768 上限 409，768x1152 上限 409 等）
VIDEO_NUM_FRAMES_BY_RESOLUTION: Dict[Tuple[int, int], int] = {
  (1152, 768): 409,   # 横屏 3:2
  (768, 1152): 409,   # 竖屏 2:3
  (832, 480):  441,   # SD 横屏
  (480, 832):  441,   # SD 竖屏
  (1088, 832): 441,   # 服务端默认横屏（协商尺寸）
  (832, 1088): 441,   # 服务端默认竖屏（协商尺寸）
}

DEFAULT_NUM_FRAMES_LIMIT = 409  # 大多数分辨率的安全上限

# ===== 中文字符检测（用于翻译预处理）=====
# 包含常用汉字 + 全角标点
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def contains_chinese(text: str) -> bool:
  """判断字符串是否包含中文字符。"""
  if not text:
    return False
  return bool(_CJK_PATTERN.search(text))


def chinese_ratio(text: str) -> float:
  """计算中文字符占总字符的比例。"""
  if not text:
    return 0.0
  cjk = sum(1 for c in text if _CJK_PATTERN.match(c))
  return cjk / max(len(text), 1)


# ===== 视频帧参数校验（v3.5+ 提取） =====
# 此前在 agnes_video.py 与 batch_video.py 复制了完全相同的两段函数。
# 统一到此处供两侧调用。

def validate_num_frames(n: int) -> None:
  """校验 num_frames：≤ VIDEO_NUM_FRAMES_LIMIT 且满足 8n+1。"""
  if n < 1 or n > VIDEO_NUM_FRAMES_LIMIT:
    raise AgnesAPIError(
      f"num_frames={n} 越界，必须在 1..{VIDEO_NUM_FRAMES_LIMIT}"
    )
  if (n - 1) % 8 != 0:
    raise AgnesAPIError(
      f"num_frames={n} 不满足 8n+1 约束，请用 1, 9, 17, 25, ..., {VIDEO_NUM_FRAMES_LIMIT}"
    )


def get_num_frames_limit(width: int, height: int) -> int:
  """根据分辨率获取 num_frames 上限（v3.5+ 新增）。

  Returns:
    该分辨率下的 num_frames 上限；若分辨率不在已知表中，返回 DEFAULT_NUM_FRAMES_LIMIT (409)。
  """
  return VIDEO_NUM_FRAMES_BY_RESOLUTION.get(
    (width, height), DEFAULT_NUM_FRAMES_LIMIT
  )


def validate_num_frames_for_resolution(n: int, width: int, height: int) -> None:
  """校验 num_frames 是否在指定分辨率的上限内（v3.5+ 新增）。

  提示：调用前先做基础 validate_num_frames(n) 校验（≤441 + 8n+1）。
  本函数专门检查"在该分辨率下服务端是否接受"。
  """
  limit = get_num_frames_limit(width, height)
  if n > limit:
    raise AgnesAPIError(
      f"num_frames={n} 超过该分辨率上限 {limit}（{width}x{height}）。"
      f"建议改为 {limit} 或更小（仍需满足 8n+1 约束）。"
      f"完整限制见 VIDEO_NUM_FRAMES_BY_RESOLUTION。"
    )


def validate_frame_rate(r: int) -> None:
  """校验 frame_rate：1..60。"""
  if r < 1 or r > 60:
    raise AgnesAPIError(f"frame_rate={r} 越界，必须在 1..60")


# ===== 错误分类 =====
# 错误类型枚举
ERR_RATE_LIMIT = "rate_limit"  # 限流（HTTP 429）
ERR_TIMEOUT = "timeout"  # 超时（连接或读取）
ERR_SERVER = "server"  # 5xx 服务端错误
ERR_CLIENT = "client"  # 4xx 客户端错误（除 429）
ERR_NETWORK = "network"  # 网络层异常（连接被拒、DNS 失败等）
ERR_UNKNOWN = "unknown"  # 未识别
# v3.5 增量 P1#6：新增网络层错误细分
#   - connection:  连接被拒 / DNS 失败 / reset → 视作服务端临时不可达
#   - chunked:     响应流截断 → 视作超时，应退避重试
#   - ssl:         TLS 错误 → 视作客户端错误（参数/环境问题），不重试


def classify_error(stderr_tail: str, status_code: Optional[int] = None) -> str:
  """
  根据 stderr_tail 和 HTTP 状态码分类错误类型。

  返回: 'rate_limit' | 'timeout' | 'server' | 'client' | 'unknown'

  优先级：status_code > stderr 关键字匹配
  """
  # 优先按状态码判断
  if status_code is not None:
    if status_code == 429:
      return ERR_RATE_LIMIT
    if 400 <= status_code < 500:
      return ERR_CLIENT
    if 500 <= status_code < 600:
      return ERR_SERVER

  if not stderr_tail:
    return ERR_UNKNOWN
  s = stderr_tail.lower()

  # 限流特征
  if "429" in s or "rate limit" in s or "rate_limit" in s or "fail_to_fetch" in s or "too many requests" in s:
    return ERR_RATE_LIMIT
  # 超时特征
  if ("read timed out" in s or "timeout" in s or "read timeout" in s
      or "timed out" in s or "connection timeout" in s):
    return ERR_TIMEOUT
  # 服务端错误
  if ("5xx" in s or "internal server" in s or "bad gateway" in s
      or "service unavailable" in s or "gateway timeout" in s):
    return ERR_SERVER
  # 客户端错误
  if ("401" in s or "403" in s or "404" in s or "invalid" in s
      or "unauthorized" in s or "forbidden" in s or "unsupportedparams" in s):
    return ERR_CLIENT
  # v3.5 增量 P1#6：网络层细分
  if "ssl" in s or "certificate" in s or "handshake" in s:
    return ERR_CLIENT  # TLS 错误不重试
  if ("connectionerror" in s or "connection refused" in s or "connection reset" in s
      or "remote end closed" in s or "name or service not known" in s):
    return ERR_SERVER  # 网络层视作服务端临时不可达
  if ("chunkedencoding" in s or "incomplete read" in s or "connection broken" in s):
    return ERR_TIMEOUT  # 流截断 → 视作超时
  return ERR_UNKNOWN


def is_retryable(err_type: str) -> bool:
  """判断该错误类型是否可重试。客户端错误一律不重试。"""
  # v3.5 增量 P1#6：network 视作可重试（网络层偶发）
  return err_type in (ERR_RATE_LIMIT, ERR_TIMEOUT, ERR_SERVER, ERR_NETWORK, ERR_UNKNOWN)


def retry_delay(err_type: str, attempt: int, consecutive_failures: int = 0,
                retry_after: Optional[int] = None) -> int:
  """
  根据错误类型、重试次数、连续失败次数计算退避秒数。

  基础退避（首次）：
    rate_limit: 60s
    timeout:    30s
    server:     15s
    client:     0s（不重试）
    unknown:    10s

  v3.5+ P1#17：Retry-After 优先
  --------------------------------
  若服务端返回了 Retry-After header（即 retry_after 不为 None），
  应优先使用服务端建议的退避时间（一般更准确），并至少 1s。
  - 限流场景（rate_limit）：Retry-After 通常是 30-300s，服务端权威
  - 超时/服务端错误：忽略 Retry-After（不适用）

  指数退避：base * 2^(attempt-1)

  持续限流检测（P1.4 改进）：若连续 3 次都是 rate_limit/timeout，
  则把 delay 拉长到 5 分钟（300s），并建议进入"冷却期"。
  """
  # v3.5+ P1#17：限流场景下 Retry-After 优先
  if retry_after is not None and retry_after > 0 and err_type == ERR_RATE_LIMIT:
    return max(int(retry_after), 1)
  base_map = {
    ERR_RATE_LIMIT: 60,
    ERR_TIMEOUT: 30,
    ERR_SERVER: 15,
    ERR_NETWORK: 20,  # v3.5 增量 P1#6
    ERR_CLIENT: 0,
    ERR_UNKNOWN: 10,
  }
  base = base_map.get(err_type, 10)
  if base == 0:
    return 0

  # 指数退避
  delay = base * (2 ** max(attempt - 1, 0))

  # 持续限流检测
  if err_type in (ERR_RATE_LIMIT, ERR_TIMEOUT) and consecutive_failures >= 3:
    delay = max(delay, 300)  # 至少 5 分钟

  return delay


# ===== 异常 =====
class AgnesAPIError(Exception):
  """Agnes API 调用错误统一封装。"""

  def __init__(self, message: str, status_code: Optional[int] = None,
               request_id: Optional[str] = None, payload: Optional[Dict[str, Any]] = None,
               error_type: Optional[str] = None):
    super().__init__(message)
    self.status_code = status_code
    self.request_id = request_id
    self.payload = payload or {}
    self.error_type = error_type or classify_error(message, status_code)

  def __str__(self) -> str:
    parts = [super().__str__()]
    if self.status_code is not None:
      parts.append(f"status={self.status_code}")
    if self.request_id:
      parts.append(f"request_id={self.request_id}")
    if self.error_type:
      parts.append(f"type={self.error_type}")
    return " | ".join(parts)


# ===== 配置 =====
@dataclass(frozen=True)
class AgnesConfig:
  """从环境变量加载的 Agnes 配置。"""
  api_key: str
  base_url: str
  http_timeout: int
  video_max_wait: int
  video_poll_interval: int
  output_dir: Path
  # 翻译相关（P1.6）
  translate_enabled: bool = False
  translate_provider: str = "stub"  # stub | google | deepl | openai
  translate_target: str = "en"
  # 批量相关
  default_interval: int = 30
  max_consecutive_failures: int = 5

  @property
  def headers(self) -> Dict[str, str]:
    return {
      "Authorization": f"Bearer {self.api_key}",
      "Content-Type": "application/json",
    }


def _candidate_env_paths() -> list[Path]:
  """
  按优先级返回可能存在的 .env 路径。

  v3.5 修复：Skill 凭据隔离
  --------------------------------
  历史行为：按 Skill 根 → 仓库根 → 用户家目录 顺序合并，override=False
  问题：当 cwd 为仓库根时，dotenv 实际优先匹配到仓库根 .env，
        且 `override=False` 在两个 .env 含相同 key 时行为不可预期，
        偶发出现"根目录 .env 覆盖 Skill .env"导致 Key 不一致。

  修复策略：**只使用 Skill 自己的 .env**（强制隔离）。
  - 行为可预测：与调用脚本的 cwd 无关
  - 安全：避免把仓库内其他项目的 .env 误加载
  - 兼容：环境变量仍可覆盖（load_dotenv override=False 保留环境变量优先）
  """
  skill_root = Path(__file__).resolve().parent.parent
  return [skill_root / ".env"]


# 真实 Key 最小长度（Agnes 平台 key 通常 40+ 字符）
_MIN_REAL_KEY_LEN = 30


def _has_repeated_chars(s: str, threshold: int = 5) -> bool:
  """检测连续重复字符超过阈值（如 'xxxxx'、'0000'）。"""
  if len(s) < threshold:
    return False
  for i in range(len(s) - threshold + 1):
    if len(set(s[i:i + threshold])) == 1:
      return True
  return False


def _is_placeholder_key(k: str) -> bool:
  """判断是否是测试/占位 Key（不应被信任）。

  v3.5+ 增强检测：
  1. 关键字（dryrun / fake / test-only / your-key / placeholder / example / xxxxx）
  2. 测试前缀（test_ / demo_ / tmp_ / sandbox_ / dev_ / mock_）
  3. 长度过短（< 30 字符大概率是占位）
  4. 连续重复字符 ≥ 5（如 'xxxxx'、'aaaaa'）
  5. 显式占位（'sk-your-...'、'sk-xxx-...'）
  """
  if not k:
    return True
  kl = k.lower()

  # 1) 关键字
  if any(tok in kl for tok in (
    "dryrun", "fake", "test-only", "test_only", "your-key", "your_key",
    "placeholder", "example", "xxxxx", "todo-", "fixme",
  )):
    return True

  # 2) 测试前缀
  for prefix in ("test_", "demo_", "tmp_", "sandbox_", "dev_", "mock_", "sample_"):
    if kl.startswith(prefix):
      return True

  # 3) 显式占位
  if kl.startswith("sk-your-") or kl.startswith("sk-xxx"):
    return True

  # 4) 长度过短
  if len(k) < _MIN_REAL_KEY_LEN:
    return True

  # 5) 连续重复字符
  if _has_repeated_chars(k, 5):
    return True

  return False


def load_config() -> AgnesConfig:
  """
  加载 Agnes 配置。

  查找顺序：环境变量 → .env（按 _candidate_env_paths 顺序）。

  v3.5+ 关键修复：占位 Key 降级
  --------------------------------
  历史行为：load_dotenv(override=False) 让"已存在的环境变量优先"。
  问题：若 PowerShell/系统残留了占位 Key（sk-dryrun-fake / sk-test-only），
        真实 .env 中的 Key 永远不会生效，导致 401。

  修复策略：检测到环境变量是"占位 Key"时，强制用 .env 覆盖。
  - 真实 .env Key → 永远生效
  - 临时占位 env（dryrun/fake/test-only）→ 自动从 .env 取真值
  - 故意用 env 覆盖真 Key（高级用法）→ 检测 key 不是 placeholder 时不覆盖
  """
  # 先用环境变量当前值
  env_key_before = os.environ.get("AGNES_API_KEY", "")
  env_is_placeholder = _is_placeholder_key(env_key_before)

  for path in _candidate_env_paths():
    if path.exists():
      # v3.5+ 修复：若 env 已有占位 Key，强制用 .env 覆盖
      # 否则保持 override=False（用户已主动设的 env 优先）
      should_override = env_is_placeholder
      load_dotenv(dotenv_path=path, override=should_override)

  api_key = os.environ.get("AGNES_API_KEY", "").strip()
  if not api_key or api_key.startswith("sk-your-") or _is_placeholder_key(api_key):
    # 最后的兜底：如果 .env 也不存在，再尝试当前工作目录的 .env（用户友好）
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
      load_dotenv(dotenv_path=cwd_env, override=True)
      api_key = os.environ.get("AGNES_API_KEY", "").strip()
  if not api_key or api_key.startswith("sk-your-") or _is_placeholder_key(api_key):
    raise AgnesAPIError(
      "AGNES_API_KEY 未配置或仍为占位符。请在 .env 中填入 platform.agnes-ai.com "
      "→ Settings → API Keys 创建的密钥。"
    )

  base_url = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
  http_timeout = int(os.environ.get("AGNES_HTTP_TIMEOUT", "60"))
  video_max_wait = int(os.environ.get("AGNES_VIDEO_MAX_WAIT", "600"))  # P1 改进：默认 600s
  video_poll_interval = int(os.environ.get("AGNES_VIDEO_POLL_INTERVAL", "5"))
  output_dir = Path(os.environ.get("AGNES_OUTPUT_DIR", "assets/outputs")).resolve()

  # 翻译配置
  translate_enabled = os.environ.get("AGNES_TRANSLATE_ENABLED", "false").lower() in ("1", "true", "yes")
  translate_provider = os.environ.get("AGNES_TRANSLATE_PROVIDER", "stub")
  translate_target = os.environ.get("AGNES_TRANSLATE_TARGET", "en")

  # 批量默认参数
  default_interval = int(os.environ.get("AGNES_BATCH_INTERVAL", "30"))
  max_consecutive_failures = int(os.environ.get("AGNES_MAX_CONSECUTIVE_FAILURES", "5"))

  return AgnesConfig(
    api_key=api_key,
    base_url=base_url,
    http_timeout=http_timeout,
    video_max_wait=video_max_wait,
    video_poll_interval=video_poll_interval,
    output_dir=output_dir,
    translate_enabled=translate_enabled,
    translate_provider=translate_provider,
    translate_target=translate_target,
    default_interval=default_interval,
    max_consecutive_failures=max_consecutive_failures,
  )


# ===== 日志 =====
def get_logger(name: str = "agnes") -> logging.Logger:
  """统一日志器，输出到 stderr，避免污染脚本主输出。"""
  logger = logging.getLogger(name)
  if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
      fmt="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
      datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
  return logger


# ===== HTTP 客户端 =====
class AgnesClient:
  """Agnes API 同步客户端。"""

  def __init__(self, config: AgnesConfig, logger: Optional[logging.Logger] = None,
               api_key: Optional[str] = None):
    self.config = config
    self.logger = logger or get_logger()
    self.session = requests.Session()
    # v3.4 优化：配置连接池（HTTPAdapter）
    # 背景：长任务中频繁调用 API + 下载视频，原 requests 默认连接池较小（pool_connections=10, pool_maxsize=10）
    # 提升到 20/40 以减少连接复用瓶颈；同时启用 retry 配置
    # 参考：POSTMORTEM 6.3 网络层优化建议
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry_cfg = Retry(
      total=3,
      backoff_factor=0.3,
      status_forcelist=(500, 502, 503, 504),
      allowed_methods=("GET", "POST"),
    )
    adapter = HTTPAdapter(
      pool_connections=20,
      pool_maxsize=40,
      max_retries=retry_cfg,
    )
    self.session.mount("http://", adapter)
    self.session.mount("https://", adapter)

    # 允许运行时覆盖 api_key（用于多 key 轮询 / 分布式）
    if api_key:
      self.session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
      })
    else:
      self.session.headers.update(config.headers)

  def __enter__(self) -> "AgnesClient":
    """v3.5+ P2#10：支持 with 语法"""
    return self

  def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    """退出时优雅关闭连接池"""
    try:
      self.session.close()
    except Exception:
      pass

  def _url(self, path: str) -> str:
    return f"{self.config.base_url}{path}"

  def post(self, path: str, json_body: Dict[str, Any],
           timeout: Optional[int] = None) -> Dict[str, Any]:
    """POST 请求，统一处理错误。"""
    url = self._url(path)
    self.logger.debug("POST %s body=%s", url, _safe_json(json_body))
    try:
      resp = self.session.post(url, json=json_body, timeout=timeout or self.config.http_timeout)
    except requests.RequestException as e:
      raise AgnesAPIError(f"网络错误: {e}", error_type=ERR_TIMEOUT) from e

    return _parse_response(resp)

  def get(self, path: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    """GET 请求，统一处理错误。"""
    url = self._url(path)
    self.logger.debug("GET %s", url)
    try:
      resp = self.session.get(url, timeout=timeout or self.config.http_timeout)
    except requests.RequestException as e:
      raise AgnesAPIError(f"网络错误: {e}", error_type=ERR_TIMEOUT) from e

    return _parse_response(resp)


def _safe_json(obj: Any) -> str:
  try:
    return json.dumps(obj, ensure_ascii=False)[:300]
  except Exception:
    return "<unserializable>"


def _parse_response(resp: requests.Response) -> Dict[str, Any]:
  """统一解析响应。"""
  request_id = resp.headers.get("x-request-id") or resp.headers.get("request-id")
  text = resp.text

  # 尝试解析 JSON
  try:
    data = resp.json()
  except ValueError:
    data = {"raw": text}

  if resp.status_code >= 400:
    msg = data.get("error", {}).get("message") if isinstance(data, dict) else None
    error_type = classify_error(msg or text, resp.status_code)
    raise AgnesAPIError(
      message=msg or f"HTTP {resp.status_code}: {text[:200]}",
      status_code=resp.status_code,
      request_id=request_id,
      payload=data if isinstance(data, dict) else {"raw": text},
      error_type=error_type,
    )

  return data if isinstance(data, dict) else {"raw": text}


# ===== 工具函数 =====
def ensure_output_dir(path: Path) -> Path:
  """确保输出目录存在。"""
  path.mkdir(parents=True, exist_ok=True)
  return path


def timestamped_filename(prefix: str, ext: str) -> str:
  """生成带时间戳的文件名。"""
  return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.{ext.lstrip('.')}"


def extract_image_url(data: Dict[str, Any]) -> str:
  """从 OpenAI 兼容的图片响应中提取 URL。"""
  if "data" in data and isinstance(data["data"], list) and data["data"]:
    item = data["data"][0]
    if isinstance(item, dict):
      if "url" in item:
        return item["url"]
      if "b64_json" in item:
        return "<base64 image data>"
  # 兜底：直接看顶层 url
  if "url" in data:
    return data["url"]
  raise AgnesAPIError(f"未找到图片 URL: {_safe_json(data)}")


def extract_image_urls(data: Dict[str, Any]) -> List[str]:
  """从响应中提取所有图片 URL。"""
  urls: List[str] = []
  if "data" in data and isinstance(data["data"], list):
    for item in data["data"]:
      if isinstance(item, dict) and "url" in item:
        urls.append(item["url"])
  if not urls and "url" in data:
    urls.append(data["url"])
  return urls


def extract_video_url(data: Dict[str, Any]) -> Optional[str]:
  """
  从视频任务响应中提取视频 URL。

  ⚠️ 文档与实现不一致：官方文档写 video_url，实际返回 remixed_from_video_id。
  这里做兼容处理。
  """
  if not isinstance(data, dict):
    return None
  # 优先官方字段，再回退到实测字段
  for key in ("video_url", "remixed_from_video_id", "url"):
    val = data.get(key)
    if isinstance(val, str) and val.startswith(("http://", "https://")):
      return val
  # 嵌套 data
  nested = data.get("data")
  if isinstance(nested, dict):
    for key in ("video_url", "remixed_from_video_id", "url"):
      val = nested.get(key)
      if isinstance(val, str) and val.startswith(("http://", "https://")):
        return val
  return None


# ===== Prompt 预处理（P1.6 改进）=====
# 简单内置词典：常见中文 prompt 关键词 → 英文改写
# 实际生产环境应使用专业翻译 API（Google / DeepL / OpenAI）
_BUILTIN_DICT: Dict[str, str] = {
  # 风格
  "赛博朋克": "cyberpunk", "写实": "photorealistic", "动漫": "anime",
  "水彩": "watercolor", "油画": "oil painting", "插画": "illustration",
  "吉卜力": "studio ghibli", "皮克斯": "pixar", "迪士尼": "disney",
  "国风": "chinese style", "中国风": "chinese traditional",
  "日系": "japanese style", "和风": "japanese style",
  # 场景
  "夜景": "night scene", "日落": "sunset", "日出": "sunrise",
  "黄昏": "dusk", "黎明": "dawn",
  # 光线
  "霓虹": "neon", "柔光": "soft light", "逆光": "backlight",
  "侧光": "side light", "自然光": "natural light",
  # 通用修饰
  "高质量": "high quality", "高细节": "high detail", "8K": "8k",
  "电影感": "cinematic", "电影级": "cinematic", "史诗": "epic",
  "梦幻": "dreamy", "唯美": "aesthetic", "清晰": "sharp",
  "细腻": "delicate", "精致": "exquisite",
  # 主体
  "少女": "young woman", "女子": "woman", "女孩": "girl",
  "男人": "man", "少年": "young man", "猫咪": "cat", "小狗": "puppy",
  "巨龙": "dragon", "龙": "dragon",
}


def _builtin_translate_zh_to_en(text: str) -> str:
  """
  简易中→英翻译（兜底实现）。

  注意：这不是真正的翻译，只是把常见中文关键词替换为英文。
  生产环境应使用 Google Translate API / DeepL API / OpenAI。
  """
  result = text
  # 词典替换（长词优先）
  for zh, en in sorted(_BUILTIN_DICT.items(), key=lambda x: -len(x[0])):
    result = result.replace(zh, en)
  # 移除残余中文字符（用英文占位）
  result = _CJK_PATTERN.sub("", result)
  # 合并多余空格
  result = re.sub(r"\s+", " ", result).strip()
  return result


def preprocess_prompt(prompt: str, config: AgnesConfig,
                       force_translate: bool = False) -> Tuple[str, Dict[str, Any]]:
  """
  Prompt 预处理统一入口（v3.3）：
    1) 应用 R1-R4 硬规则（结构化透传 / CJK 注入 / 敏感词保留 / aspect_ratio 提取）
    2) R1 透传时跳过翻译
    3) 否则检测中文 → 翻译为英文

  返回：(处理后 prompt, 元信息 dict)
  """
  meta = {
    "was_chinese": False,
    "translated": False,
    "provider": None,
    "original_chars": len(prompt),
    "translated_chars": 0,
    "chinese_ratio": round(chinese_ratio(prompt), 3),
    "r_rules": [],
    "r_action": "rewritten",
    "r_structured": False,
    "r_cjk_injected": False,
    "r_sensitive_preserved": False,
    "r_inferred_nationality": "",
    "aspect_ratio": "",
    "size": (1024, 1024),
  }

  if not prompt:
    return prompt, meta

  # v3.3 第 1 步：R1-R4 硬规则
  rule_result = apply_hard_rules(prompt)
  prompt = rule_result["rewritten"]
  meta["r_rules"] = rule_result["rules_applied"]
  meta["r_action"] = rule_result["action"]
  meta["r_structured"] = rule_result["structured"]
  meta["r_cjk_injected"] = rule_result["cjk_injected"]
  meta["r_sensitive_preserved"] = rule_result["sensitive_preserved"]
  meta["r_inferred_nationality"] = rule_result["inferred_nationality"]
  meta["aspect_ratio"] = rule_result["aspect_ratio"]
  meta["size"] = rule_result["size"]

  # v3.3 第 2 步：R1 透传 → 跳过翻译
  if rule_result["structured"]:
    meta["translated_chars"] = len(prompt)
    return prompt, meta

  # 第 3 步：中文翻译
  ratio = chinese_ratio(prompt)
  meta["was_chinese"] = ratio > 0.1

  should_translate = force_translate or (config.translate_enabled and ratio > 0.1)
  if not should_translate:
    meta["translated_chars"] = len(prompt)
    return prompt, meta

  if config.translate_provider == "google":
    translated = _google_translate(prompt, config.translate_target)
  elif config.translate_provider == "openai":
    translated = _openai_translate(prompt, config.translate_target)
  else:
    translated = _builtin_translate_zh_to_en(prompt)

  meta["translated"] = True
  meta["provider"] = config.translate_provider
  meta["translated_chars"] = len(translated)
  return translated, meta


def _google_translate(text: str, target: str) -> str:
  """Google Translate API 占位实现（需要 GOOGLE_TRANSLATE_API_KEY）。"""
  return _builtin_translate_zh_to_en(text)


def _openai_translate(text: str, target: str) -> str:
  """OpenAI 翻译占位实现（需要 OPENAI_API_KEY）。"""
  return _builtin_translate_zh_to_en(text)


# ===== 批量运行器 =====
@dataclass
class BatchStats:
  """批量运行统计。"""
  total: int = 0
  success: int = 0
  failed: int = 0
  timeout: int = 0
  error: int = 0
  consecutive_failures: int = 0
  max_consecutive: int = 5
  error_types: Dict[str, int] = field(default_factory=dict)
  start_time: float = field(default_factory=time.time)
  end_time: Optional[float] = None

  def record(self, result: Dict[str, Any]) -> None:
    """记录一次结果。"""
    self.total += 1
    status = result.get("status", "unknown")
    if status == "success":
      self.success += 1
      self.consecutive_failures = 0
    else:
      if status == "failed":
        self.failed += 1
      elif status == "timeout":
        self.timeout += 1
      else:
        self.error += 1
      self.consecutive_failures += 1
    et = result.get("error_type")
    if et:
      self.error_types[et] = self.error_types.get(et, 0) + 1

  @property
  def success_rate(self) -> float:
    return self.success / max(self.total, 1)

  @property
  def elapsed(self) -> float:
    return (self.end_time or time.time()) - self.start_time

  def to_dict(self) -> Dict[str, Any]:
    return {
      "total": self.total,
      "success": self.success,
      "failed": self.failed,
      "timeout": self.timeout,
      "error": self.error,
      "success_rate": round(self.success_rate, 4),
      "elapsed": round(self.elapsed, 1),
      "error_types": dict(self.error_types),
      "consecutive_failures": self.consecutive_failures,
    }
