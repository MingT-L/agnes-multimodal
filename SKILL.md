---
name: "agnes-multimodal"
description: "调用 Agnes 多模态 API（Image 2.1 Flash / 2.0 Flash / Video V2.0）生成图片或视频。Invoke when 用户说画/生图/生成图片/做封面/改图/图生图/生视频/做个小视频/agnes/Agnes。"
---

# Agnes 多模态（生图 / 生视频）

本 Skill 把 [Agnes AI](https://agnes-ai.com) 的图片与视频模型封装为可在 Trae 对话中直接调用的能力。**它由 `SKILL.md`（指令层）+ `scripts/`（执行层）组成**，模型按本文件规则引导对话并调用本地 Python 脚本完成实际 API 调用。

## 〇、版本与改进记录

| 版本 | 日期 | 主要改进 |
|---|---|---|
| v1 | 2026-06-14 | 初版（生图 / 生视频 / 图生图） |
| v2 | 2026-06-15 | 引入 `auto_test.py` v2 框架（interval + 智能 retry） |
| v3 | 2026-06-15 | 集成 POSTMORTEM.md 全部 11 条改进（详见下文） |
| **v3.1** | **2026-06-15** | **批量脚本专项优化**（batch_image / batch_video 新增 6 共享参数 + 1 视频专用） |
| **v3.2** | **2026-06-15** | **整合 tip.md 4 条 prompt 改写硬规则**（5.4 R1-R4；自检清单 + 错误处理同步）|
| **v3.3** | **2026-06-16** | **R1-R4 统一入口 + 全链路覆盖**（见下） |
| **v3.4** | **2026-06-16** | **P1/P2 持续优化**：状态机文档化、翻译缓存 SHA256、partial sidecar、heartbeat 自适应、HTTPAdapter 连接池 |
| **v3.5** | **2026-06-16** | **P1#6 网络层错误细分**（ERR_NETWORK + TLS/连接/流截断三类映射 + 20s 退避）|
| **v3.5+** | **2026-06-16** | **Skill 体检与 P0/P1/P2 修复**：硬编码路径去除、路径穿越加固、翻译缓存路径统一、validate 去重、partials 隔离、R5 防护、Retry-After 优先 |
| **v3.5+ P0#5** | **2026-06-16** | **占位 Key 自动降级**（`_is_placeholder_key` 5 维检测：关键字/前缀/长度/重复字符/显式占位；避免 PowerShell 残留 env 覆盖真 Key）|
| **v3.5+ P0#23** | **2026-06-17** | **根目录治理 + 工具沉淀**：清理残留文件、`_is_placeholder_key` 增强、scripts/agnes-real-generate.py 可复用、R6 风险词预过滤 |
| **v3.5.1** | **2026-06-17** | **本次会话驱动优化**：（1）`--prompt-file` 参数支持；（2）按分辨率的 `num_frames` 实际限制表 + 预校验；（3）task.json 保存原始/处理后 prompt + R 规则；（4）R5 移除 `young/youth` 误判词；（5）多行 prompt 提示 + 诊断目录隔离规范 |

### v3 主要改进（对应 POSTMORTEM 11 条建议）

| 编号 | 改进项 | 落地方式 |
|---|---|---|
| P0.1 | 默认带 interval + retries | `auto_test.py` / `batch_*.py` 默认 30s 间隔 |
| P0.2 | 串行执行不并发 | `batch_*` 默认串行；明确不推荐与生图并发 |
| P0.3 | 客户端错误快速失败 | `is_retryable()` 跳过 4xx 重试 |
| P1.4 | 智能限流退避 | `retry_delay()` 连续 3 次 → 5 分钟冷却 |
| P1.5 | 失败 case 单独 retry | `auto_test.py --retry-failed` |
| P1.6 | 中文 prompt 翻译 | `prompt_translator.py` + `AGNES_TRANSLATE_ENABLED` |
| P1.7 | 单次多张（生图） | `batch_image.py --n 4` |
| P2.8 | Web UI 监控 | `monitor_web.py` |
| P2.9 | PDF 报告 | `pdf_report.py` |
| P2.10 | 分布式执行 | `auto_test.py --worker-id / --total-workers` |
| P2.11 | LLM 错误分类 | `llm_classifier.py` + `auto_test.py --use-llm-classify` |

---

## 一、模型与端点速查

| 能力 | 模型 | 端点 | 同步/异步 |
|---|---|---|---|
| 文生图 | `agnes-image-2.1-flash` | `POST /v1/images/generations` | 同步（~5s） |
| 图生图 / 编辑 | `agnes-image-2.0-flash` | 同上（`extra_body` 携带参考图） | 同步 |
| 文生视频 | `agnes-video-v2.0` | `POST /v1/videos` + 轮询 `GET /v1/videos/{task_id}` | **异步（1–3 min）** |

> Base URL 默认 `https://apihub.agnes-ai.com/v1`，鉴权 `Authorization: Bearer <AGNES_API_KEY>`。

## 二、触发条件

当用户消息命中以下任一意图时启用本 Skill：

- **生图**：「画一张 / 生成图片 / 做个封面 / 出个图 / 海报 / 配图 / 帮我画 / 画只猫 / 设计一个 logo / 批量生成 10 张」
- **改图/图生图**：「把这张图改成… / 换成水彩风格 / 帮我修一下图 / 局部编辑 / 去掉背景」
- **生视频**：「生成视频 / 做个视频 / 来段短视频 / 拍一个 / 文生视频 / 几秒的视频 / agnes video / 批量生成 5 个视频」
- **明确调用**：「@agnes / agnes image / agnes video / 用 Agnes」

不确定时，先简短确认一次：「你要生图还是生视频？告诉我画面描述就行。」

## 三、调用流程

### 3.1 通用规则

1. **检查凭据**：脚本启动时会自动读 `.env`（`AGNES_API_KEY`、`AGNES_BASE_URL`）。若用户首次使用且未配置，给出指引：
   - 申请地址：`https://platform.agnes-ai.com` → Settings → API Keys
   - **必须**复制 `.env.example` 为 `.env` 并放在 **Skill 根目录**（不要放项目根）：
     ```
     d:\vs\MiMo-Code\.trae\skills\agnes-multimodal\.env
     ```
   - ⚠️ **不要**放在项目根目录（虽然脚本有兜底逻辑会读，但违反"凭据隔离"设计；项目根 `.gitignore` 中 `.env` 的保护可能不如技能目录强）
2. **prompt 改写**：把用户的口语化描述改写成高密度 prompt（参考 `examples/prompts.md`）。**若输入已是结构化 prompt（JSON / 字段:值 / `[VISUAL]:` 标记 / 多行镜头脚本）则不进行改写**，直接透传。详见 [5.4 prompt 改写硬规则](#54-prompt-改写硬规则来自-tipmd)。
3. **中文检测**：若 prompt 中文字符占比 > 10%，且 `AGNES_TRANSLATE_ENABLED=true`，自动翻译为英文（P1.6）。
4. **选择脚本与模式**：
   - 单条生图：`scripts/agnes_image.py`（文生图）
   - 单条改图：同上 + `--ref-image <URL>`
   - 单条视频：`scripts/agnes_video.py`
   - **批量生图**：`scripts/batch_image.py --file prompts.txt --n 2`（P1.7 + 智能限流）
   - **批量生视频**：`scripts/batch_video.py --file prompts.txt --duration 5`（状态感知轮询 + 翻译）
   - **自动化测试**：`assets/auto_test.py --type image --count 100`（v3 框架，集成全部 11 条改进）
5. **执行**：通过 Trae 的脚本执行能力调用 Python 脚本，把生成的图片/视频路径反馈给用户。
6. **错误处理**：捕获 `AgnesAPIError` 并按错误码提示（见第六节）。

### 3.2 脚本位置

```
.trae/skills/agnes-multimodal/
├── SKILL.md                 # 本文件
├── scripts/
│   ├── agnes_common.py      # 公共：配置/客户端/异常/翻译/错误分类
│   ├── agnes_image.py       # 单条文生图 / 图生图
│   ├── agnes_video.py       # 单条文生视频（异步轮询）
│   ├── prompt_translator.py # P1.6 中文 → 英文翻译
│   ├── batch_image.py       # 批量生图（--n 多张 + 限流）
│   ├── batch_video.py       # 批量生视频（智能轮询 + 翻译）
│   ├── monitor_web.py       # P2.8 Web UI 监控
│   ├── pdf_report.py        # P2.9 PDF 报告
│   └── llm_classifier.py    # P2.11 LLM 错误分类
├── examples/
│   └── prompts.md           # prompt 模板
├── assets/outputs/          # 默认下载目录（首次调用自动创建）
├── .env.example             # 凭据样例（含 P1.6 翻译配置）
└── .gitignore
```

执行命令的 cwd 用仓库根（`d:\vs\MiMo-Code`），脚本路径用相对路径。

## 四、典型对话模板

### 4.1 单条生图

> 用户：帮我画一张猫咪在海边散步的图片，黄昏写实风格
>
> 模型（内部）：
> 1. 改写 prompt：`A cat walking on the beach at sunset, soft golden light, photorealistic, gentle waves, high detail, 35mm photography`
> 2. 调用：
>    ```bash
>    python .trae/skills/agnes-multimodal/scripts/agnes_image.py \
>      "A cat walking on the beach at sunset, soft golden light, photorealistic, gentle waves, high detail, 35mm photography" \
>      --size 1024x1024
>    ```
> 3. 返回本地路径：`assets/outputs/image_01_20260615_103000.png`
> 4. 反馈给用户：「已生成，路径：… 你可以预览一下」

### 4.2 批量生图（10+ 张）

> 用户：帮我批量生成 10 张不同风格的猫
>
> 模型（内部）：
> 1. 准备 prompt 列表（每行一条）
> 2. 写入临时文件 `assets/outputs/_prompts_10cats.txt`
> 3. 调用批量脚本（**P1.7 单次多张 + P0 防限流**）：
>    ```bash
>    python .trae/skills/agnes-multimodal/scripts/batch_image.py \
>      --file assets/outputs/_prompts_10cats.txt \
>      --n 2 \
>      --interval 30 \
>      --out-dir assets/outputs/batch_cats
>    ```
> 4. 输出：`batch_cats/log_<时间戳>.json`（含每张图的 URL 和路径）

### 4.3 单条视频

> 用户：生成一个 10 秒的赛博朋克城市夜景视频
>
> 模型（内部）：
> 1. 改写 prompt：`Cinematic cyberpunk city at night, neon lights reflecting on wet streets, flying cars, 10 seconds, smooth camera dolly forward, 24fps, atmospheric, Blade Runner style`
> 2. **翻译检查**（P1.6）：如果用户输入是中文且 `AGNES_TRANSLATE_ENABLED=true`，自动翻译
> 3. 提示用户：**视频生成通常需要 1-3 分钟**
> 4. 调用：
>    ```bash
>    python .trae/skills/agnes-multimodal/scripts/agnes_video.py \
>      "Cinematic cyberpunk city at night, neon lights, flying cars, smooth dolly forward" \
>      --duration 10
>    ```
> 5. 返回路径：`assets/outputs/video_<id>_20260615_103500.mp4`

### 4.4 批量生视频（5+ 个）

> 用户：批量生成 5 个 5 秒的产品展示视频
>
> 模型（内部）：
> 1. 准备 prompt 列表
> 2. 调用批量脚本（**状态感知轮询 + 智能限流 + 中文翻译**）：
>    ```bash
>    python .trae/skills/agnes-multimodal/scripts/batch_video.py \
>      --file prompts.txt \
>      --duration 5 \
>      --interval 30 \
>      --max-wait 600
>    ```
> 3. 输出：`assets/outputs/batch_video/log_<时间戳>.json`

### 4.5 自动化测试（100+ 个）

> 用户：跑 100 个生图测试
>
> 模型（内部）：
> 1. 调用 `auto_test.py` v3 框架（集成全部 11 条改进）：
>    ```bash
>    python assets/auto_test.py --type image --count 100 --interval 30
>    ```
> 2. 框架自动：
>    - 解析 README.md 提取 100 个 prompt
>    - 每个 case 之间间隔 30s（P0.1）
>    - 中文 prompt 自动翻译（P1.6，依赖 `AGNES_TRANSLATE_ENABLED`）
>    - 失败智能重试（P0.3 + P1.4）
>    - 持续失败进入冷却期
>    - 进度持久化（中断可续跑）
> 3. 输出：`assets/auto_test_logs/image_<时间戳>.json` + `.md` 报告

## 五、参数选择指南

### 5.1 图片尺寸

| 用途 | 推荐 size |
|---|---|
| 通用方形（社媒/封面） | `1024x1024` |
| 横屏（博客/Banner） | `1152x768` |
| 竖屏（手机壁纸/小红书） | `768x1152` |
| 电影宽屏 | `1024x768` |

### 5.2 视频时长与 num_frames 上限

**通用公式**：`seconds = num_frames / frame_rate`，`num_frames` 必须满足 `8n+1` 且 ≤441。

**v3.5+ 实测按分辨率上限表**（v3.5+ 实测数据，替换早期"全局 ≤441"的过松文档）：

| 分辨率 (W×H) | num_frames 上限 | 实际可达时长 @24fps | 备注 |
|---|---|---|---|
| `1152×768` (横屏 3:2) | **409** | 17.0s | 文档中常见的横屏 |
| `768×1152` (竖屏 2:3) | **409** | 17.0s | 文档中常见的竖屏 |
| `1088×832` (横屏 13:10) | 441 | 18.4s | 服务端协商尺寸 |
| `832×1088` (竖屏 10:13) | 441 | 18.4s | 服务端协商尺寸 |
| `832×480` (SD 横屏) | 441 | 18.4s | SD 备选 |
| `480×832` (SD 竖屏) | 441 | 18.4s | SD 备选 |
| 其他分辨率 | 409 | 17.0s | 保守默认值 |

**预设时长参考表**（最常用组合）：

| 目标时长 | 推荐 num_frames | frame_rate | 实际验证 |
|---|---|---|---|
| 5s  | 121 | 24 | ✅ 所有分辨率都支持 |
| 10s | 241 | 24 | ✅ 所有分辨率都支持 |
| 15s | 361 | 24 | ✅ `1152x768` 等受限分辨率（8*45+1） |
| 17s | 409 | 24 | ✅ `1152x768` / `768x1152` 极限 |
| 18s | 441 | 24 | ⚠️ **仅 1088x832 / 832x480 等支持**；`1152x768` 会 400 |

> ⚠️ **重要**：
> 1. v3.5+ 之前文档写"≤441"是错误的——`1152x768` / `768x1152` 实际是 409
> 2. 脚本会预校验并在超出时立即报错（`AgnesAPIError: num_frames=N 超过该分辨率上限 L`）
> 3. 18s 视频如果必须用 `1152x768`，请改用 **17s（409 帧）**

### 5.3 prompt 改写要素

把模糊描述改写为高密度 prompt 时，至少包含：

1. **主体**（subject）：明确的对象
2. **动作 / 状态**（action / state）
3. **环境 / 场景**（setting）
4. **风格**（style）：写实/插画/水彩/3D/电影感
5. **光线**（lighting）：暖光/侧光/霓虹
6. **构图 / 镜头**（composition）：广角/特写/俯拍
7. **画质**（quality）：high detail, 8k, sharp

详见 `examples/prompts.md`。

### 5.4 prompt 改写硬规则（来自 tip.md）

下列规则为 prompt 处理的**硬约束**，**优先级高于** 5.3 改写要素和 5.5 批量任务最佳实践。违反任何一条都视为 SKILL 行为缺陷。

| # | 规则 | 应用判定 | 示例 |
|---|---|---|---|
| **R1** | **结构化 prompt 不改写** | 输入若是结构化形式（JSON / 字段:值 / `[VISUAL]:` 标记 / 多行镜头脚本 / 摄影参数块），**直接透传**，不要扩写、不要"优化"、不要补字段。 | `"A cat, 35mm, f/1.4, golden hour"` → 透传；不补成"a cat walking in a park with..." |
| **R2** | **人物默认中日韩** | 提示词里**有"人物/女人/男人/girl/boy/woman/man/model/character"等主体**但**未指定国家/人种**时，默认设定为**中国 / 日本 / 韩国**三国之一的特征（面部、服饰、场景都应匹配该国风格）。 | `"A young woman sitting in a cafe"` → 默认中国/日本/韩国女性；不是欧美人；不是非洲人；不是中东人。 |
| **R3** | **不降级、不去除敏感措辞** | 当用户输入的 prompt **本身**包含敏感词（如 voluptuous / cleavage / nudity 等）时，**不要主动降级为更保守的措辞**，**不要用同义词替换**，**不要"为了通过审核"而删除**。 | 输入 `"voluptuous figure, deep cleavage"` → 保留原样；不替换为"slender figure, modest neckline"。 |
| **R4** | **明确列出需保留的敏感词** | `voluptuous`、`cleavage` 等词**必须保留**，不要移除、不要降级、不要用委婉词替换。 | 同 R3 示例。 |

**实现要点**：

- R1 通过格式识别判断（出现 `{` `:` `[VISUAL]` `[SPEECH]` 之一即视为结构化）
- R2 在改写环节添加"如果有人物主体 + 无国家信息 → 注入 'East Asian' / 'Chinese' / 'Japanese' / 'Korean'"
- R3 / R4 禁止对输入 prompt 做正则替换、敏感词过滤、同义词替换等"主动审查"操作；如服务端拒接，再以原始 prompt 提示用户调整，而不是自动改写

**参考实现与验证**：

- 仓库根目录 `test_tip_rules.py` 提供了 `apply_hard_rules()` 函数，可直接 import 使用
- 包含 32 个测试用例覆盖 R1-R4 全部场景：结构化 JSON / 镜头脚本 / 摄影参数 / 人物无国家 / 已指定 american / 已指定中国 / voluptuous / cleavage / 结构化+敏感词组合
- 关键不变量：`voluptuous` 永远不被替换为 `slender`，`cleavage` 永远不被替换为 `modest`

**与第六章"错误处理"的关系**：

- 第六章"视频任务 failed → 简化 prompt 后重试，避免敏感词"指的是**系统/模型自动注入的额外敏感内容**（如被改写环节添加的），**不**指用户原始 prompt 中的 voluptuous / cleavage 等。
- 重试时应**保留**原始敏感词，**去除**改写时额外添加的修饰。

### 5.5 批量任务最佳实践（POSTMORTEM 关键经验）

| 经验 | 建议 |
|---|---|
| **串行不并发** | 不要把"批量生图"和"批量生视频"同时跑，会触发 Agnes 单用户限流（POSTMORTEM 问题 #3） |
| **间隔要够** | 生视频每条至少 30s 间隔；生图每条至少 15-30s |
| **失败 retry** | 跑完一轮后用 `--retry-failed` 再跑一轮失败 case |
| **中文先翻译** | 视频 API 对中文 0% 成功率，必须翻译 |
| **长视频要长超时** | 10s+ 视频至少 600s 超时；15s+ 视频 1200s |
| **冷却期** | 连续 5 次失败 → 自动 5 分钟冷却，避免触发更严格限流 |

### 5.6 多行 / 长 prompt 处理（v3.5.1 新增）

**问题场景**：
- Windows PowerShell 的 PSReadLine 处理多行 prompt 时会触发 `ArgumentOutOfRangeException` 异常
- 包含换行符的 prompt 在 shell 转义中容易损坏
- 含特殊字符（`"`, `'`, `$`, `;`）的 prompt 在命令行直接传参容易出错

**推荐方案 1：`--prompt-file`（v3.5.1 新增参数）**

```bash
# 把 prompt 写到文件
echo "A young dancer performing a Chinese-inspired street dance..." > my_prompt.txt

# 通过文件读取
python .trae/skills/agnes-multimodal/scripts/agnes_video.py \
  --prompt-file my_prompt.txt \
  --width 1152 --height 768 --num-frames 401

# 同样适用于 agnes_image.py
python .trae/skills/agnes-multimodal/scripts/agnes_image.py \
  --prompt-file my_prompt.txt --size 1024x1024
```

**推荐方案 2：Python runner 模式**（适合复杂调用）

```python
# run_dance.py
import sys
from pathlib import Path

prompt = Path("prompts/dance.txt").read_text(encoding="utf-8")
sys.argv = [
    "agnes_video.py", prompt,
    "--width", "1152", "--height", "768",
    "--num-frames", "401", "--max-wait", "900",
    "--out-dir", "assets/outputs/my_dance",
]
sys.path.insert(0, ".trae/skills/agnes-multimodal/scripts")
from agnes_video import main
sys.exit(main())
```

**诊断测试隔离（v3.5.1 规范）**：

- 生产输出放 `assets/outputs/<项目名>/`
- 诊断/测试用 prompt 放 `assets/_debug/`（带下划线前缀，不会被业务脚本扫描）
- 临时 runner 脚本命名 `_runner_<场景>.py`（下划线前缀），可在 `.gitignore` 中排除

**为什么不能直接用命令行传多行 prompt**：
1. PowerShell PSReadLine 的渲染 bug：长多行 prompt 在交互式终端会触发渲染异常
2. 即使绕过 PSReadLine，shell 转义（`"`、`$`、反引号）会破坏内容
3. 中间产物（`_prompt.txt` 等）如果放在产品目录会污染后续任务

### 5.7 输出目录与 .gitignore 规范（v3.5.1 新增）

**输出目录命名建议**：
```
assets/outputs/
├── <项目名>/                 # 业务输出（提交到 Git 时用 .gitignore 排除）
│   ├── *.mp4 / *.png        # 实际产物
│   ├── *.task.json          # 任务元数据
│   └── _prompt.txt          # 可选：保存原始 prompt 便于复现
├── _debug/                  # 诊断/测试（永远 gitignore）
└── _archive/                # 历史废弃产物
```

**推荐 `.gitignore` 规则**（仓库根目录）：
```gitignore
# Agnes 多模态输出（业务产物可能很大，不入库）
assets/outputs/**/*.mp4
assets/outputs/**/*.png
assets/outputs/**/_prompt.txt
assets/outputs/**/_runner.py
assets/outputs/_debug/
assets/outputs/_archive/

# 但保留任务元数据（体积小，便于追溯）
!assets/outputs/**/*.task.json
!assets/outputs/**/.gitkeep
```

## 六、错误处理与降级

| 现象 | 原因 | 处理 |
|---|---|---|
| `AGNES_API_KEY 未配置` | `.env` 没填或路径不对 | 提示用户在仓库根或 Skill 根创建 `.env` |
| `HTTP 401 / 403` | Key 无效或过期 | 提示重新生成 API Key（**不重试**） |
| `HTTP 429` | 触发 RPM 限流 | 等待 60-300s 后重试；连续 3 次触发 5 分钟冷却 |
| `HTTPSConnectionPool: Read timed out` | 网络超时 | 重试 1-2 次；可调大 `AGNES_HTTP_TIMEOUT=120` |
| `UnsupportedParamsError: response_format` | 文生图传了 `response_format` | 切到图生图（2.0 Flash），或不传 extra_body |
| `num_frames 不满足 8n+1` | 帧数算错 | 改用 `--duration 5/10/18` 让脚本自动算 |
| 视频任务 `failed` | 内容审核/参数越界 | 简化 prompt 后重试，**去除改写时自动添加的修饰词**；**保留**用户原始 prompt 中的敏感词（见 5.4 R3/R4）|
| 视频任务超时（>600s） | 服务端排队 | 把 task_id 暴露给用户，下一轮用 `GET /v1/videos/{task_id}` 重试 |
| `video_url` 字段缺失 | 文档与实现不一致 | 脚本已自动兼容 `remixed_from_video_id` |
| 中文 prompt 视频失败率高 | Agnes 视频对中文支持弱 | 启用 `AGNES_TRANSLATE_ENABLED=true`（P1.6） |

## 六.5、真实生成验证案例（v3.5+）

### 案例 1：占位 Key 降级（v3.5+ P0#5）

**场景**：PowerShell 进程残留了环境变量 `AGNES_API_KEY=sk-dryrun-fake-key-for-test-only`，导致 47 次图片生成全部 401。

**根因**：`load_dotenv(override=False)` 不会覆盖已存在的环境变量，stale env 永远会"劫持"真 .env Key。

**修复**：[agnes_common.py:295-333](file:///D:/vs/MiMo-Code/.trae/skills/agnes-multimodal/scripts/agnes_common.py#L295-L333) 的 `_is_placeholder_key()` 5 维检测：
1. 关键字：dryrun / fake / test-only / your-key / placeholder / example / xxxxx
2. 测试前缀：test_ / demo_ / tmp_ / sandbox_ / dev_ / mock_ / sample_
3. 显式占位：sk-your- / sk-xxx
4. 长度过短（< 30 字符）
5. 连续重复字符 ≥ 5

**验证**：47/47 张图片全部 success，0 个 401。

### 案例 2：服务端内容审核预过滤（v3.5+ R6）

**观察**：9 张图片因 prompt 含 `plasma sword / samurai / chrome armor` 等被服务端 400 拒绝。

**修复**：[hard_rules.py:332-399](file:///D:/vs/MiMo-Code/.trae/skills/agnes-multimodal/scripts/hard_rules.py#L332-L399) 新增 `find_risky_patterns` + `rewrite_risky_prompt`，15 类风险词 → 友好替代。

**效果**：在 `apply_hard_rules` 第 0.5 步预判并自动重写，预计可将 400 失败率从 19% 降到 < 5%。

### 案例 3：一键验证工具（v3.5+ P0#23）

**使用**：
```bash
python scripts/agnes-real-generate.py --kind image --n 20
python scripts/agnes-real-generate.py --kind video --n 10 --duration 5
python scripts/agnes-real-generate.py --kind both --n 5
```

**关键设计**：
- 自动清空 `AGNES_API_KEY` 环境变量，避免 stale env 干扰
- 占位符自动补全（不再按字面渲染 `[SPHERE OBJECT]`）
- 内置 success/failed/401 汇总

### 案例 4：根目录治理（v3.5+ P0#23）

**修复前**：根目录堆积 47+ 个临时文件（`_20img.log`、`_verify_*.py` 等），污染仓库结构。

**修复**：
- [`.gitignore`](/.gitignore) 增加 `_*.log`、`/_run_*.py` 等规则（路径式写法，仅根目录生效）
- 历史验证脚本迁移到 `assets/auto_test_logs/_test_scripts/`
- 临时文件清理后，根目录整洁

## 七、安全与凭据

- **绝对不要**在对话、commit、SKILL.md 中出现真实 `AGNES_API_KEY`
- `.env` 已在 `.gitignore` 中
- 团队共享时只复制 `.env.example`，每个人填自己的 Key
- 任何包含 `<YOUR_KEY>` 的占位符都视为未配置

## 八、扩展与工具（v3 全部落地）

| 工具 | 用途 | 命令 |
|---|---|---|
| `prompt_translator.py` | 中文 → 英文翻译 | `python scripts/prompt_translator.py "赛博朋克少女"` |
| `batch_image.py` | 批量生图（智能限流） | `python scripts/batch_image.py --file prompts.txt --n 2` |
| `batch_video.py` | 批量生视频（状态感知轮询） | `python scripts/batch_video.py --file prompts.txt --duration 5` |
| `monitor_web.py` | Web UI 实时监控 | `python scripts/monitor_web.py --port 8765` |
| `pdf_report.py` | 报告转 PDF | `python scripts/pdf_report.py --log log.json --out report.pdf` |
| `llm_classifier.py` | LLM 错误分类 | `python scripts/llm_classifier.py "HTTP 429"` |
| `auto_test.py` (assets/) | 自动化测试 v3 | `python assets/auto_test.py --type image --count 100` |

### 8.1 批量脚本专项优化（v3.1 新增参数）

`batch_image.py` 和 `batch_video.py` 共享以下 6 个新参数：

| 参数 | 作用 | 使用场景 |
|---|---|---|
| `--shuffle` | 随机打乱 prompt 顺序 | 避免连续相同模式触发服务端缓存限流 |
| `--seed N` | 随机种子（可复现） | 与 `--shuffle` 配合，结果可重现 |
| `--early-stop N` | 连续 N 个失败后自动停止 | 大批量跑测时遇到持续失败不必再耗时间 |
| `--exclude-errors T1,T2` | 跳过指定错误类型 | 例如已知 rate_limit 失败，可排除后只跑有意义的 |
| `--no-preflight` | 跳过启动前 API 健康检查 | 已知 API 健康时节省 1-2s 启动时间 |
| `--dry-run` | 干跑：只翻译 + 校验，不调 API | 上线前快速验证 prompt 集、估算翻译数 |

`batch_video.py` 额外支持：

| 参数 | 作用 | 使用场景 |
|---|---|---|
| `--reuse-task` | 续跑时复用历史 task_id | 视频生成是异步任务，中断后直接 poll 旧 ID 即可，**避免重复扣费** |

**典型用法**：

```bash
# 1) 先 dry-run 验证 prompt 集
AGNES_TRANSLATE_ENABLED=true python batch_image.py --file zh_prompts.txt --dry-run

# 2) 大批量跑测：随机 + 早停 + 排除限流
python batch_image.py --file prompts.txt --n 4 --shuffle --seed 42 \
  --early-stop 10 --exclude-errors rate_limit,timeout

# 3) 视频中断后用 --reuse-task 续跑（不重复创建任务）
python batch_video.py --file prompts.txt --duration 5 --resume log_xxx.json --reuse-task
```

### 8.2 推荐工作流（v3.1 优化版）

```
┌────────────────────────────────────────────────────────┐
│ 1. 准备 prompts.txt                                    │
│ 2. --dry-run 验证（不扣费、不打 API）                    │
│ 3. 小规模试跑（3-5 条）+ --shuffle --early-stop 3       │
│ 4. 全量跑：--interval 30 --shuffle --seed N --n 4      │
│ 5. 失败重跑：--retry-failed 或 --reuse-task（视频）     │
│ 6. 出 PDF 报告：pdf_report.py --out report.pdf         │
└────────────────────────────────────────────────────────┘
```

## 八.5、批量任务状态机（v3.3+）

`batch_image.py` 与 `batch_video.py` 共用 4 种状态，下游消费方应据此区分处理：

| 状态 | 语义 | 触发场景 | 下游建议 |
|---|---|---|---|
| `success` | 完全成功（生成 + 下载） | API 返回成功 + 本地文件存在 | 直接使用 `saved_path`（本地路径）|
| `partial` | 生成成功但下载失败 | API 返回成功 URL，但 `download_video()` 失败 | **可重试**：用 `task_id` 重新轮询获取 URL 再下载；或保留 `video_url` 链接给用户 |
| `failed` | 生成失败或重试耗尽 | 4xx/5xx 错误、限流超时、客户端错误 | 检查 `error_type`，区分 `client`（不重试）vs `rate_limit/timeout/server`（可重试） |
| `skipped` | 主动跳过 | `--exclude-errors` 命中、或被 `--early-stop` 触发后续 case 跳过 | 统计展示用，不计入失败率 |

**关键不变量**（v3.3 起强制）：

1. `success` 状态一定有本地 `saved_path`（且不是 URL）
2. `partial` 状态一定有 `video_url` 字段 + `error` 字段
3. `failed` 状态一定有 `error_type` 字段（5 类之一）
4. `skipped` 状态一定有 `skipped_reason` 字段

**判断 saved_path 是本地文件还是 URL 的方法**：

```python
if saved_path.startswith(("http://", "https://")):
    # 是 URL（可能对应 success/no_download/partial 三种状态之一）
else:
    # 一定是本地文件（只能是 success）
```

## 八.6、partial 状态补救流程

当某条视频 `status=partial` 时，可按以下流程补救：

```bash
# 1) 从日志读取 partial 列表
python -c "
import json
log = json.load(open('log_xxx.json'))
partial = [(k, v) for k, v in log.items() if v.get('status') == 'partial']
print(f'共 {len(partial)} 个 partial:')
for k, v in partial:
    print(f'  {k}: task_id={v.get(\"task_id\")} url={v.get(\"video_url\")[:60]}')
"

# 2) 手动下载 partial（用 task_id 重试即可）
python -c "
import requests
from pathlib import Path
url = 'https://xxx/video.mp4'  # 从日志中复制
dest = Path('assets/outputs/batch_video/recovered_xxx.mp4')
resp = requests.get(url, stream=True, timeout=600)
with dest.open('wb') as f:
    for chunk in resp.iter_content(64*1024):
        f.write(chunk)
print(f'已下载: {dest}')
"
```

## 九、未来扩展方向

- **MCP Server 化**：把 `scripts/` 封装为 MCP server，让 Claude Desktop / Cursor 等也能调用
- **图生视频**：当前仅文生视频，未来可加 `image` 字段（参考官方文档）
- **本地缓存**：在 `assets/outputs/.cache/` 维护 prompt → result 索引，避免重复生成
- **结果预览**：生图后自动在对话面板插入 markdown 图片
- **更多分布式 worker**：当前 `auto_test.py` 支持多 worker shard，可对接 K8s / Celery 任务队列

## 十、调用前自检清单

执行前快速过一遍：

- [ ] 用户意图是「生图 / 图生图 / 生视频 / 批量 / 自动化测试」哪一种？
- [ ] `.env` 里的 `AGNES_API_KEY` 存在且非占位符？
- [ ] prompt 是否已经改写为高密度描述？
- [ ] **（5.4 R1）** 输入是结构化 prompt（JSON / 字段:值 / `[VISUAL]:` 标记 / 多行镜头脚本）吗？若是 → **直接透传，不改写**
- [ ] **（5.4 R2）** 提示词有"人物"主体但未指定国家/族裔吗？若是 → 默认补全为**中国/日本/韩国**之一
- [ ] **（5.4 R3/R4）** 用户 prompt 中含 voluptuous / cleavage 等词吗？若是 → **保留原样**，不降级、不替换、不删除
- [ ] 图片选了合适的 `size`？视频选了 `--duration` 还是手动算 num_frames？
- [ ] 是否需要提前告知用户视频生成耗时（1-3 分钟）？
- [ ] 改图/图生图模式下，是否已拿到参考图 URL？
- [ ] 批量任务是否设了 `--interval` 防限流？
- [ ] 中文 prompt 场景是否启用 `AGNES_TRANSLATE_ENABLED`？
- [ ] 是否避免与"生图批量"和"生视频批量"并发执行？
- [ ] **（v3.1 新）** 大批量跑测前是否先 `--dry-run` 验证 prompt 集？
- [ ] **（v3.1 新）** 视频续跑是否用 `--reuse-task` 避免重复扣费？

满足后调用对应脚本，把本地路径或 URL 反馈给用户。

---

**v3.1 更新日志**：在 v3 基础上对 `batch_image.py` / `batch_video.py` 实施专项优化，新增 6 个共享参数 + 1 个视频专用参数。完整测试 18/18 通过，向后兼容（所有旧参数照常工作）。
