# Agnes 多模态 Skill

在 Trae 对话中通过自然语言调用 [Agnes AI](https://agnes-ai.com) 的 **图片** 与 **视频** 生成能力。

- 文生图：`agnes-image-2.1-flash`
- 图生图 / 编辑：`agnes-image-2.0-flash`
- 文生视频：`agnes-video-v2.0`

> **v3 版本（2026-06-15）**：基于 11 条 POSTMORTEM 改进建议全面优化。新增 5 个工具脚本，支持批量生图/生视频、prompt 中文翻译、Web 监控、PDF 报告、LLM 错误分类、分布式执行。详见 [SKILL.md §〇](./SKILL.md)。

## 目录结构

```
.trae/skills/agnes-multimodal/
├── SKILL.md                 # Skill 主体：触发条件、调用流程、错误处理
├── scripts/
│   ├── agnes_common.py      # 公共：配置 / HTTP 客户端 / 异常 / 日志 / 翻译 / 错误分类
│   ├── agnes_image.py       # 单条文生图 / 图生图 CLI
│   ├── agnes_video.py       # 单条文生视频 CLI（异步轮询）
│   ├── prompt_translator.py # 中文 → 英文翻译（P1.6）
│   ├── batch_image.py       # 批量生图（--n 多张 + 智能限流）
│   ├── batch_video.py       # 批量生视频（状态感知轮询 + 翻译）
│   ├── monitor_web.py       # Web UI 实时监控面板（P2.8）
│   ├── pdf_report.py        # PDF 报告生成器（P2.9）
│   └── llm_classifier.py    # LLM 错误分类器（P2.11）
├── examples/
│   └── prompts.md           # 高质量 prompt 模板库
├── assets/outputs/          # 默认下载目录（自动创建）
├── .env.example             # 凭据样例（v3 扩展：含翻译配置）
└── .gitignore
```

## 快速开始

### 1. 安装 Python 依赖

```bash
pip install python-dotenv requests
```

可选依赖（按需）：
```bash
pip install reportlab    # PDF 报告
pip install openai       # LLM 错误分类 / 翻译
```

### 2. 申请 API Key

1. 访问 [platform.agnes-ai.com](https://platform.agnes-ai.com) 注册账号
2. 进入 `Settings → API Keys → Create new secret key`
3. 复制 Key（**只显示一次**）

### 3. 配置 .env

```bash
# ⚠️ 重要：必须放在 Skill 根目录，不是项目根
#   ❌ 不推荐：d:\vs\MiMo-Code\.env（项目根）
#   ✅ 推荐：  d:\vs\MiMo-Code\.trae\skills\agnes-multimodal\.env（Skill 根）
cp .trae/skills/agnes-multimodal/.env.example .trae/skills/agnes-multimodal/.env
# 然后填入真实 AGNES_API_KEY
```

> **为什么是 Skill 根？**
> 1. 技能脚本 v3.5+ 已修复为"强制只读 Skill 自己的 .env"
> 2. Skill 根目录的 `.gitignore` 已经排除 `.env`（更安全）
> 3. 未来加其他技能时，**各技能的 key 互相隔离**，不会污染
> 4. 项目根 `.env` 仍能 work（脚本有兜底逻辑），但不推荐

### 4. 验证（文生图）

```bash
python .trae/skills/agnes-multimodal/scripts/agnes_image.py \
  "A cute shiba inu under cherry blossom, soft sunlight, cinematic" \
  --size 1024x1024
```

成功后会下载图片到 `assets/outputs/image_01_<时间戳>.png`。

### 5. 验证（视频）

```bash
python .trae/skills/agnes-multimodal/scripts/agnes_video.py \
  "A cute shiba inu under cherry blossom, soft sunlight, cinematic" \
  --duration 5
```

视频生成通常需要 **1-3 分钟**，脚本会自动轮询，完成后下载到 `assets/outputs/`。

## 进阶使用

### 批量生图（10+ 张）

```bash
# 准备 prompts.txt（每行一条）
cat > prompts.txt <<EOF
A cat walking on the beach at sunset
A dog running in the snow
A bird flying over mountains
EOF

# 批量：单次 2 张 + 30s 间隔
python .trae/skills/agnes-multimodal/scripts/batch_image.py \
  --file prompts.txt --n 2 --interval 30
```

输出：`assets/outputs/batch_image/log_<时间戳>.json`（含每张图的 URL 和本地路径）。

### 批量生视频

```bash
python .trae/skills/agnes-multimodal/scripts/batch_video.py \
  --file prompts.txt --duration 5 --interval 30
```

输出：`assets/outputs/batch_video/log_<时间戳>.json`。

### 批量脚本专项优化参数（v3.1 新增）

`batch_image.py` 和 `batch_video.py` 共享 6 个新参数：

| 参数 | 作用 |
|---|---|
| `--shuffle` `--seed N` | 随机打乱（避免连续相同模式触发限流，可复现） |
| `--early-stop N` | 连续 N 个失败后自动停止 |
| `--exclude-errors T1,T2` | 跳过指定错误类型 |
| `--no-preflight` | 跳过启动前 API 健康检查 |
| `--dry-run` | 干跑：只翻译 + 校验，不打 API |

`batch_video.py` 额外支持 `--reuse-task`（续跑时复用历史 task_id，避免重复扣费）。

**推荐工作流**：

```bash
# 1) 先 dry-run 验证 prompt 集
AGNES_TRANSLATE_ENABLED=true python batch_image.py --file zh.txt --dry-run

# 2) 大批量跑测：随机 + 早停 + 排除限流
python batch_image.py --file prompts.txt --n 4 --shuffle --seed 42 \
  --early-stop 10 --exclude-errors rate_limit,timeout

# 3) 视频中断后用 --reuse-task 续跑（不重复创建任务）
python batch_video.py --file prompts.txt --duration 5 \
  --resume log_xxx.json --reuse-task
```

### 自动化测试（100+ 个）

从 README.md 自动提取 prompt 并批量测试：

```bash
# 100 个生图测试
python assets/auto_test.py --type image --count 100 --interval 30

# 100 个视频测试（自动翻译中文 prompt）
AGNES_TRANSLATE_ENABLED=true python assets/auto_test.py --type video --count 100

# 失败 case 单独 retry
python assets/auto_test.py --type image --retry-failed --log logs/image_xxx.json

# 分布式：第 1 段（共 3 段）
python assets/auto_test.py --type image --count 300 --worker-id 0 --total-workers 3
```

输出：`assets/auto_test_logs/<type>_<时间戳>.json` + `.md` 报告。

### Web UI 实时监控

```bash
python .trae/skills/agnes-multimodal/scripts/monitor_web.py --port 8765
# 浏览器打开 http://localhost:8765
```

显示总览卡片、错误类型分布、最近运行、日志详情。3 秒自动刷新。

### PDF 报告

```bash
pip install reportlab
python .trae/skills/agnes-multimodal/scripts/pdf_report.py \
  --log assets/auto_test_logs/image_100.json \
  --out assets/auto_test_logs/report.pdf
```

### LLM 错误分类

```bash
# 单条分类
python .trae/skills/agnes-multimodal/scripts/llm_classifier.py "HTTP 429 too many requests"

# 集成到 auto_test
python assets/auto_test.py --type image --count 50 --use-llm-classify
# 需配置 OPENAI_API_KEY
```

## 在 Trae 对话中使用

只要对话里出现"画 / 生图 / 改图 / 做视频 / 批量生成"等意图，模型会自动启用本 Skill。
详细触发词与对话模板见 [SKILL.md](./SKILL.md)。

### 对话示例

> **你**：帮我画一张猫咪在海边散步的图片，黄昏写实风格
>
> **模型**：（自动改写 prompt + 调用脚本 + 反馈本地路径）

> **你**：把上面这张图改成水彩风格
>
> **模型**：（用上一张图 URL 走图生图模式）

> **你**：生成 10 秒的赛博朋克城市夜景
>
> **模型**：（提前提示耗时 1-3 分钟，调用视频脚本）

> **你**：批量生成 10 张不同风格的猫
>
> **模型**：（调用 `batch_image.py`，智能限流 + 续跑）

## CLI 参数速查

### 单条脚本

| 脚本 | 关键参数 |
|---|---|
| `agnes_image.py` | `--size`, `--n`, `--ref-image`, `--out-dir`, `--no-download`, `--json` |
| `agnes_video.py` | `--duration 5/10/18`, `--num-frames`, `--frame-rate`, `--max-wait`, `--poll-interval` |

### 批量脚本（v3 新增）

| 脚本 | 关键参数 |
|---|---|
| `batch_image.py` | `--file`, `--n 1-4`, `--interval 30`, `--retries 3`, `--out-dir`, `--resume`, `--force-translate` |
| `batch_video.py` | `--file`, `--duration 5/10/18`, `--interval 30`, `--max-wait 600`, `--resume` |
| `monitor_web.py` | `--host`, `--port 8765`, `--log-dir` |
| `pdf_report.py` | `--log`, `--md`, `--out` |
| `llm_classifier.py` | `--error`, `--from-log`, `--api-key`, `--model` |

### auto_test.py v3 新增参数

| 参数 | 说明 |
|---|---|
| `--n 1-4` | 生图单次多张（P1.7） |
| `--retry-failed` | 失败 case 单独 retry（P1.5） |
| `--translate` | 启用 prompt 翻译（P1.6） |
| `--worker-id / --total-workers` | 分布式分片（P2.10） |
| `--use-llm-classify` | LLM 错误分类（P2.11） |

## 配置项（v3 扩展）

完整配置见 [.env.example](./.env.example)，关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGNES_API_KEY` | (必填) | API 密钥 |
| `AGNES_BASE_URL` | `https://apihub.agnes-ai.com/v1` | API 入口 |
| `AGNES_HTTP_TIMEOUT` | 60 | HTTP 请求超时（秒） |
| `AGNES_VIDEO_MAX_WAIT` | **600**（v3 改进） | 视频任务最大等待（秒） |
| `AGNES_BATCH_INTERVAL` | 30 | 批量 case 之间间隔（防限流） |
| `AGNES_MAX_CONSECUTIVE_FAILURES` | 5 | 冷却期触发阈值 |
| `AGNES_TRANSLATE_ENABLED` | false | 是否启用中文 → 英文翻译 |
| `AGNES_TRANSLATE_PROVIDER` | stub | 翻译 provider：stub / google / openai |
| `AGNES_TRANSLATE_TARGET` | en | 翻译目标语言 |

## 常见问题

### Q: 报 `AGNES_API_KEY 未配置或仍为占位符`

检查：
- `.env` 文件是否在 **仓库根** 或 **Skill 根** 或 **用户家目录** 任一位置
- Key 是否还是 `sk-your-real-key-here`
- 复制时是否带了多余空格

### Q: 视频一直 pending / 排队很久

Agnes 视频 API 单用户有 QPS 限制（POSTMORTEM 实测 ~1 req/30s）。
- 默认 600s 超时，可调大 `AGNES_VIDEO_MAX_WAIT`
- 超时后用 `--save-task` 拿到 task_id，下一轮单独轮询
- **不要与生图批量并发**（POSTMORTEM 问题 #3）

### Q: 文生图报错 `UnsupportedParamsError: response_format`

这是**官方踩坑**——纯文生图（`agnes-image-2.1-flash`）不支持 `response_format`。
本脚本已规避：只有切换到 `agnes-image-2.0-flash`（图生图）时才传。

### Q: 视频响应里没有 `video_url` 字段

官方文档写的是 `video_url`，实测返回的是 `remixed_from_video_id`。
本脚本已自动兼容两个字段名。

### Q: 中文 prompt 视频失败率高

Agnes 视频 API 对中文支持弱（实测 0% 成功率）。**必须翻译**：

```bash
# 方法 1：环境变量
AGNES_TRANSLATE_ENABLED=true python .trae/skills/agnes-multimodal/scripts/batch_video.py --file zh.txt

# 方法 2：CLI
python .trae/skills/agnes-multimodal/scripts/prompt_translator.py "赛博朋克少女"
```

### Q: 想批量生成

- **生图**：`batch_image.py --file prompts.txt --n 2 --interval 30`
- **视频**：`batch_video.py --file prompts.txt --duration 5 --interval 30`
- **大规模测试**：`assets/auto_test.py --type image --count 100`

### Q: 想图生视频 / 关键帧动画？

官方 Video V2.0 文档未来可能扩展。当前 SKILL 只覆盖**文生视频**。
可在本目录下加 `scripts/agnes_i2v.py` 自行扩展。

## v3 改进记录

| 编号 | 改进 | 收益 |
|---|---|---|
| P0.1 | 默认带 interval + retries | 视频成功率 24% → 93% |
| P0.2 | 串行执行不并发 | 杜绝并发降速 |
| P0.3 | 客户端错误快速失败 | 节省无意义重试 |
| P1.4 | 智能限流退避 | 持续 3 次失败 → 5 分钟冷却 |
| P1.5 | 失败 case 单独 retry | 自动恢复率 +20% |
| P1.6 | 中文 prompt 翻译 | 中文 case 0% → 70%+ |
| P1.7 | 单次多张（--n 4） | 生图吞吐 ×2 |
| P2.8 | Web UI 监控 | 实时进度可视化 |
| P2.9 | PDF 报告 | 可分享的归档格式 |
| P2.10 | 分布式执行 | 多 worker 加速 |
| P2.11 | LLM 错误分类 | 精细化失败分析 |

详细数据见 [POSTMORTEM.md](../../assets/auto_test_logs/POSTMORTEM.md)。

## v3.1 改进记录（专项优化）

针对 `batch_image.py` 和 `batch_video.py` 实施专项优化，新增以下能力：

| 改进 | 描述 | 收益 |
|---|---|---|
| `--shuffle` + `--seed` | 随机打乱 prompt 顺序 | 避免连续相同模式触发服务端缓存限流 |
| `--early-stop N` | 连续 N 个失败自动停止 | 大批量跑测不必耗到最后一个 case |
| `--exclude-errors` | 跳过指定错误类型 | 例如已知 rate_limit 失败的 case 排除后跑 |
| `--no-preflight` | 跳过 API 健康检查 | 已知健康时省 1-2s 启动时间 |
| `--dry-run` | 干跑：只翻译+校验 | 上线前快速验证 prompt 集，**不扣费** |
| `--reuse-task`（仅视频）| 续跑复用历史 task_id | 视频中断后**不重复扣费**、直接 poll 旧任务 |
| ETA 估算 | 实时显示剩余时间 | UX 改善，便于评估长任务 |

测试覆盖：18/18 专项优化验证 + 47/47 核心冒烟测试 + 38/38 POSTMORTEM 落地验证，全部通过。

## 安全

- **不要**把真实 API Key 提交到 Git
- **不要**在对话里贴出 Key
- 凭据文件一律走 `.env` + `.gitignore`

## 依赖

| 包 | 用途 | 必需 |
|---|---|---|
| `python-dotenv` | 读取 .env | ✅ |
| `requests` | HTTP 客户端 | ✅ |
| `reportlab` | PDF 报告 | 可选 |
| `openai` | LLM 错误分类 / 翻译 | 可选 |

最低 Python 版本：3.8+。

## 路线图

- [x] v3 集成 11 条 POSTMORTEM 改进（2026-06-15）
- [ ] 接入 MCP server，跨 IDE 复用
- [ ] 图生视频支持
- [ ] 本地结果缓存（避免重复生成）
- [ ] A/B 测试框架（新旧 prompt 对比）

## 许可

仅作为个人开发工具，按 Agnes AI 平台服务条款使用。
免费 API 计划**不提供 SLA**，请勿作为生产基础设施。
