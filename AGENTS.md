# AGENTS.md - Edge-TTS API 智能助手开发与维护指南

> **适用对象**：所有协同开发、维护、部署此项目的 AI 代理（Antigravity、Claude Code、Cursor Agent、Copilot 等）与人类开发者。  
> **核心原则**：本项目以“高可用、零破坏性变更、环境自洽、隐私安全”为第一准则。任何 Agent 接手本项目时，**必须首先阅读并严格遵守本文档**。

---

## 1. 项目定位与核心特性

本项目是一个基于 **FastAPI** 和微软 Edge 大模型语音合成协议构建的**生产级高可用 TTS 文本转语音服务与一体化智能工作台**。

- **核心价值**：突破官方限制，支持超长文本智能弹性切片、独家单并发防封控防频控队列、MP3 实长线性时间轴校准、以及原文句级时间戳精确插入。
- **开箱即用 Web 工作台**：纯原生现代化单页界面，内置 14 款精选角色（播音、叙事、情感、方言），静态音色试听 MP3 完全本地化，Web Audio API 原生清脆提示音，暗色 cURL Demo 卡片（真实 Token 复制机制）。
- **完全开源与脱敏**：项目历史提交已彻底扁平化净化，严禁引入任何硬编码生产密钥或内部 IP 凭证。

---

## 2. 项目目录结构规范

```text
.
├── app/
│   ├── main.py              # FastAPI 核心服务 (接口路由、防封队列、时间轴校准、Token鉴权、模板注入)
│   ├── edge_tts_runner.py   # 增强调度器 (Sec-MS-GEC v2 签名、主干与备用 WebSocket 端点容灾、Locale 日期解析)
│   ├── drm_v2.py            # Sec-MS-GEC v2 算法核心实现 (支持 edge-tts < 7.3.1 旧容器环境免重建热补丁)
│   ├── requirements.txt     # 运行时核心 Python 依赖 (必须锁定 edge-tts>=7.3.1)
│   ├── static/voices/*.mp3  # 14 款预设角色的本地试听 MP3 音频 (受 Git 白名单保护)
│   └── templates/index.html # Web 工作台一体化前端 (Vanilla JS + SVG 图标 + Web Audio 提示音)
├── docker-compose.yml       # Docker Compose 生产编排 (镜像自洽构建、卷挂载配置)
├── Dockerfile               # 独立自洽容器镜像构建文件 (集成阿里云高速源加速)
├── tests/
│   └── test_core.py         # 核心时间轴缩放、时间戳插入、动态时限与日期解析单元测试
├── docs/
│   ├── CONVERSATION_HISTORY.md # 全局对话历史复盘档案 (严格记录每一次修改、用户Prompt与Token消耗)
│   └── archive/             # 历史旧文档与原始 HTML 原型归档
├── .env.example             # 环境变量配置模板
├── AGENTS.md                # 本文档 (所有 Agent 必须遵守的维护手册)
└── README.md                # 面向最终用户的项目使用说明
```

---

## 3. 核心业务逻辑与技术细节

### 3.1 微软 WebSocket 端点与防 403 / 429 容灾机制

1. **Sec-MS-GEC v2 复合签名**：
   - 微软官方自 2026 年 10 月起对 `speech.platform.bing.com` 强制启用 v2 签名（16 字符 PatentHash 与 64 字符 SHA256 拼接，协议版本 `Sec-MS-GEC-Version: 2-...`）。
   - `edge-tts >= 7.3.1` 原生支持 v2 签名；
   - 若宿主机容器尚未重新构建依赖（仍为 7.2.8），`app/edge_tts_runner.py` 会自动通过 `app/drm_v2.py` 进行**内存无感热注入**，确保无需重建容器即可立即恢复。
2. **端点优先级与容灾轮换**：
   - **第一优先级（主干端点）**：`wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=...`（容量大、官方主干、无 429 频控限制）。
   - **第二优先级（应急备用）**：`wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key=...`。
   - 当遇到 `WSServerHandshakeError` 且状态码为 `403` 或 `429` 时，自动降级至备选端点重试。
3. **时钟偏移校准 (Clock Skew)**：
   - 采用 `email.utils.parsedate_to_datetime` 解析服务端返回的 `Date` 头，防止非英文 Locale 系统下 `strptime` 崩溃抛出 `SkewAdjustmentError`。

### 3.2 子进程调用与看门狗设计

- **为什么采用子进程而不是直接调库**：
  微软在线朗读属于反向工程协议，网络波动或微软服务端假死可能导致异步 WebSocket 连接无限期挂起。通过 `asyncio.create_subprocess_exec` 启动独立进程执行，并在外层配置 `wait_process_with_stream_watchdog`，能确保一旦触发超时熔断，可通过 `process.kill()` 彻底杀死子进程并回收操作系统句柄，绝不会污染 FastAPI 主事件循环。
- **看门狗规则**：
  - **动态长文时限**：基础 300 秒，长文每 100 字符增加 15 秒，最高放宽至 900 秒；
  - **流式活跃探测**：每秒检测一次输出 MP3 文件大小，只要文件在增大（流式数据正常涌入），任务就不中断；
  - **静默假死熔断**：若持续 60 秒无任何字节流入，立即强行中断回收。

### 3.3 音频时长探测与 SRT 字幕重缩放 (`resync_srt`)

- 微软返回的 SRT 字幕时间戳是基于理论分词计算的，常常与合成的实际 MP3 音频时长存在 0.1s~1.5s 的偏差。
- 服务端使用 `mutagen.mp3.MP3` 精准探测实际生成的音频物理时长，计算缩放因子 `scaling_factor = actual_duration / original_duration`，对 SRT 中每个条目的开始和结束时间进行高精度线性重校准。

---

## 4. 环境变量与配置参考

所有环境变量均支持通过 `.env` 或系统环境变量传入覆盖：

| 变量名 | 默认值 | 作用与说明 |
| :--- | :--- | :--- |
| `PORT` | `36485` | 宿主机映射服务端口 |
| `API_TOKEN` | `your_secure_api_token_here` | API 鉴权密钥。留空则免鉴权模式；配置后接口需提供 `X-API-Token` 或 `Authorization: Bearer <token>` |
| `NAS_SHARE_PATH` | `./nasShare/` | 宿主机持久化目录路径（支持绝对路径如 `/vol1/1000/400G/nasShare/`） |
| `OUTPUT_DIR` | `/nasShare/edgeTTSoutputs` | 容器内输出文件目录 |
| `FILE_LIFETIME_HOURS` | `192` | 文件在服务端保留时长（192 小时 = 8 天），由后台协程自动清理过期文件 |
| `TTS_MAX_CONCURRENCY` | `1` | 访问微软端点的最大并发数（防封控核心，推荐保持 1） |
| `TTS_MAX_RETRIES` | `3` | 失败重试次数（包含指数退避抖动 1.5s, 3.5s...） |
| `TTS_TIMEOUT_SECONDS` | `300` | 任务基础超时时限（秒） |
| `TTS_INACTIVITY_TIMEOUT`| `60` | 流式数据静默无响应超时阈值（秒） |
| `TTS_COOLDOWN_SECONDS` | `0.5` | 两次请求之间的安全防频控微冷却间隔（秒） |
| `DEFAULT_PROXY` | 可选 | 上游 HTTP/SOCKS 代理地址（如 `http://127.0.0.1:7890`） |
| `TZ` | `Asia/Shanghai` | 容器时区 |

---

## 5. Agent 必须严格恪守的铁律 (Agent Invariants)

在对本项目进行任何修改或建议时，**必须无条件执行以下规则**：

1. **绝对向后兼容 (Zero Breaking Changes)**：
   - 严禁修改现有接口端点路径（`/tts`、`/list-files`、`/downloads/{filename}`、`/health`、`/`、`/ui`）；
   - 严禁修改 `TTSRequest` 的字段名称、类型及默认值（`text`, `voice`, `rate`, `volume`, `pitch`, `proxy`, `filename`）；
   - 严禁修改 `/tts` 响应的 JSON 字段键名（`message`, `media_url`, `subtitles_url`, `subtitles_content`, `processed_text`）。
2. **严防敏感信息泄漏 (No Secrets in Git)**：
   - 严禁将真实生产 Token、私网 IP、内网域名、私有 NAS 路径硬编码提交至 Git；
   - 所有的文档、代码示例、测试脚本中必须使用统一安全占位符（如 `your_secure_api_token_here`, `http://127.0.0.1:36485`）。
3. **保持自洽构建与干净仓库**：
   - 绝不能将宿主机 `site-packages` 挂载覆盖入容器，绝不能将 `site-packages` 提交入库；
   - 容器内更新依赖统一通过 `app/requirements.txt` 和 `Dockerfile` 进行；
   - 历史音频和字幕由 `.gitignore` 过滤，严禁提交音频垃圾文件入库。
4. **对话复盘与 Token 追踪记录义务 (Mandatory Tracking)**：
   - 每次对话执行修改后，**必须计算本轮交互消耗的 Token 数与历史累计 Token 数**；
   - 必须将本轮的用户原始需求（Original Prompt）、修改深度复盘、提交 Commit 记录及 Token 统计追加写入 [`docs/CONVERSATION_HISTORY.md`](file:///home/net67373/edge-tts-api/docs/CONVERSATION_HISTORY.md)；
   - 在回复用户的内容末尾，必须主动陈述本轮 Token 消耗量及累计消耗量。
5. **同步维护本文档 (`AGENTS.md`)**：
   - 每次对系统架构、端点、环境变量或关键流程做出调整后，**必须同步更新本文件**，确保下一个接手的 Agent 拥有最准确的全局上下文。

---

## 6. 常用开发与测试指令

```bash
# 1. 运行本地核心算法测试（必须全部通过）
python3 -m unittest tests/test_core.py

# 2. 模拟容器启动与验证
docker compose up -d --build

# 3. 查看容器服务日志
docker compose logs -f app

# 4. 进入容器或升级依赖测试
docker compose exec app pip list
```
