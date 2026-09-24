# Edge-TTS API & 智能语音合成工作台

高健壮性、防微软 IP 封控的 Microsoft Edge-TTS 文本转语音（TTS）Web 服务与一体化控制台。

内置**单并发排队防封控**、**指数退避自动重试**、**字幕时间轴自动重校准**、**文稿时间戳自动打点**与**可视化 Web 交互工作台**。严格保持原有 API 接口参数 100% 向后兼容。

---

## 🌟 核心特性

- **🛡️ 防封控并发排队机制**：内置 `asyncio.Semaphore` 信号量队列，默认单并发（`TTS_MAX_CONCURRENCY=1`）向微软服务器发送请求，避免多路并发冲击触发微软 WebSocket 频控（1006 异常 / 403 Forbidden 封禁 IP）。
- **🔁 智能重试与流式看门狗**：具备输出文件流式数据活跃检测（只要音频持续流入就不中断，支持长文生成），结合 60s 无响应假死熔断与长文动态超时（基础 300s，长文自动弹性放宽至 15 分钟），对断流或异常自动执行最多 3 次指数退避重试，使生成成功率提升至 >99%。
- **🎯 字幕与音频毫秒级重校准**：通过 `mutagen` 解析生成 MP3 的实际时长，对 SRT 字幕时间轴进行线性伸缩重同步（`resync_srt`），彻底解决长文音频与字幕不同步问题。
- **⏱️ 原文稿自动插入时间戳**：根据 SRT 字幕的句子开始时间，自动在文稿对应位置插入 `[time=xx:xx:xx,xxx]` 标记，便于剪辑软件或播放器联动。
- **🖥️ 一体化现代化 Web 交互台**：
  - **上半部（语音生成器）**：14 款精选中文角色试听卡片（男声、女声、童声、方言、台式普通话等）、实时字数统计、语速/音调/音量微调、自定义文件名与代理配置、生成结果多标签实时预览与一键复制。
  - **下半部（生成历史列表）**：按北京时间倒序排列、内置互斥音频播放器、直达下载链接与一键复制。
- **🔒 安全与生命周期管控**：支持 `X-API-Token` / Bearer 鉴权；严格防御路径穿越（Path Traversal）；后台常驻定时清理任务（默认保留 8 天 / 192 小时，可自由配置）。
- **🚀 零侵入接口兼容**：严格保持原有的 `POST /tts` 参数命名、默认值与响应结构完全一致，调用方系统无须任何改动。

---

## 🏗️ 健壮性与防封架构设计

Edge-TTS 基于微软 Edge 浏览器的免费大声朗读接口（WebSocket 传输），容易因以下原因生成失败：
1. **多请求并发冲击**：同一 IP 短时间内建立多个 WebSocket 连接，微软端点会返回 403 或直接重置连接。
2. **网络波动与长连接假死**：偶发性丢包或握手超时导致进程卡死。
3. **残留脏数据**：生成失败的破损音频未及时清理影响后续判定。

**优化方案：**

```
客户端请求 (Web / API)
        │
        ▼
   [API Token 鉴权]
        │
        ▼
[异步并发队列 (Semaphore = 1)] ──► 防止并发过多导致微软封 IP
        │
        ▼
   [执行循环 (最大重试 3 次)]
        │──► 流式活动守护 (持续有音频数据流入不中断)
        │──► 假死熔断 (60s 无数据流入强制中断) & 动态长文时限 (300s~900s)
        │──► 失败异常捕获 & 清除残余文件
        │──► 指数退避抖动等待 (1.5s, 3.5s...)
        │
        ▼
  [MP3 实际时长探测] ──► [SRT 时间轴重缩放] ──► [文稿插入时间戳]
        │
        ▼
  返回结果 & 自动刷新 Web 历史
```

---

## 🚀 快速开始

### 方式 1：Docker Compose 运行（推荐）

项目针对 Docker 部署进行了完整优化，**全新克隆仓库直接运行即可，无需手动配置环境**：

```bash
# 启动服务（自动本地构建独立自洽镜像，默认映射主机 36485 端口）
docker compose up -d

# 查看运行日志
docker compose logs -f

# 升级依赖或重新构建
docker compose up -d --build
```

> **💡 独立自洽构建说明**：
> - 采用 `Dockerfile` 自动构建模式，所有依赖（`edge-tts`、`fastapi`、`uvicorn`、`mutagen` 等）在构建时一次性打入镜像内部，并已集成阿里云备选镜像加速。
> - 容器启动时间缩短至 **0.2 秒**，启动时**无需实时联网访问 PyPI**，彻底杜绝了动态下载超时的网络问题，以及挂载宿主机 `site-packages` 造成的跨架构冲突与版本回溯（`ResolutionImpossible`）报错。
> - 源码目录 `./app` 与生成文件目录 `./nasShare` 依然双向挂载到宿主机，日常修改业务代码即时生效，生成历史与音频完整持久化。

服务启动后，在浏览器访问：
```
http://<服务器IP>:36485/
```

### 方式 2：本地 Python 直接运行

```bash
# 1. 安装依赖
pip install -r app/requirements.txt

# 2. 运行服务
cd app
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

访问 `http://127.0.0.1:8000/` 即可打开 Web 工作台。

---

## 📖 Web 工作台使用指南

1. **环境与 API Token 自动化**：
   - 鉴权 Token 直接在服务端通过环境变量 `API_TOKEN` 统一配置（支持 `.env` 或 `docker-compose.yml`）。
   - 工作台页面加载时服务端自动安全注入，无需手动在网页重复输入，同时 Demo 代码区提供复制与星号掩码功能。
2. **选择音色与本地试听**：
   - 包含云扬、云健、晓晓、晓依、云希、云霞、东北小北、陕西小妮、粤语（晓佳/晓曼/云龙）、台湾普通话（晓臻/晓雨/云哲）等 14 种精选角色（默认音色：云扬）。
   - 音色试听音频已全部本地化存储于 `app/static/voices/`，无需依赖外部第三方 CDN，点击卡片即开即播，互斥播放。
3. **输入与调整**：
   - 在左侧文稿输入框编写文本，实时统计字符数。
   - 可展开“高级参数调节”微调语速（如 `+15%`）、音量（如 `+0%`）、音调（如 `+0Hz`）、自定义文件名或专属代理。
4. **生成与获取**：
   - 点击“使用「云扬」音色朗读”（随选定音色动态变更），系统通过单并发防封队列安全调用微软端点。
   - 生成成功后自动播放清脆的“叮”提示音（不自动外放播放长音频），右侧即时呈现音频播放器与下载按钮，并可在选项卡中查看/一键复制 SRT 字幕或时间戳原文。
5. **底部多标签页（生成历史 / API 代码）**：
   - **生成历史**：默认激活，标题旁支持实时搜索过滤；服务端文件默认保存 8 天（192 小时）到期自动清理；具备智能感知机制，有新生成文件时静默自动刷新，无需手动点击。表格采用紧凑中文字距排版，所有表头（含操作）统一左对齐。
   - **API 代码**：提供随时可用的 cURL 示例，自动感知当前访问域名与端口，一键复制完整可用命令。

---

## 🔌 API 接口文档

### 1. 文本转语音 (POST `/tts`)

- **URL**: `/tts`
- **Method**: `POST`
- **Headers**:
  - `Content-Type: application/json`
  - `X-API-Token: <your_token>` （如果配置了 API_TOKEN）
- **请求体 (JSON Body)**：

| 字段 | 类型 | 必填 | 默认值 | 描述 |
| :--- | :--- | :--- | :--- | :--- |
| `text` | string | **是** | - | 要转换为语音的文稿内容 |
| `voice` | string | 否 | `zh-CN-XiaoxiaoNeural` | 语音角色名称 |
| `rate` | string | 否 | `+0%` | 语速调整，如 `+20%`, `-10%` |
| `volume` | string | 否 | `+0%` | 音量调整，如 `+10%`, `-20%` |
| `pitch` | string | 否 | `+0Hz` | 音调调整，如 `+50Hz`, `-30Hz` |
| `proxy` | string | 否 | `null` | 代理服务器，如 `http://127.0.0.1:7890` |
| `filename` | string | 否 | `null` | 自定义文件名（不含扩展名），重名自动编号 |

- **请求示例 (curl)**：

```bash
curl -X POST "http://localhost:36485/tts" \
  -H "Content-Type: application/json" \
  -H "X-API-Token: your_secure_api_token_here" \
  -d '{
      "text": "你好，这是经过高健壮性防封优化后的 Edge-TTS 语音服务。",
      "voice": "zh-CN-XiaoxiaoNeural",
      "rate": "+0%",
      "filename": "demo-audio"
  }'
```

- **响应格式 (200 OK)**：

```json
{
  "message": "TTS generated successfully.",
  "media_url": "http://localhost:36485/downloads/demo-audio.mp3",
  "subtitles_url": "http://localhost:36485/downloads/demo-audio.srt",
  "subtitles_content": "1\n00:00:00,000 --> 00:00:03,850\n你好，这是经过高健壮性防封优化后的 Edge-TTS 语音服务。\n",
  "processed_text": "[time=00:00:00,000]你好，这是经过高健壮性防封优化后的 Edge-TTS 语音服务。"
}
```

---

### 2. 获取历史文件列表 (GET `/list-files`)

- **URL**: `/list-files`
- **Method**: `GET`
- **Headers**: `X-API-Token: <your_token>`
- **响应示例**：

```json
[
  {
    "filename": "demo-audio.mp3",
    "size": 65420,
    "modified_at": "2026-09-07T07:45:00+00:00",
    "url": "http://localhost:36485/downloads/demo-audio.mp3"
  }
]
```

---

### 3. 下载文件 (GET `/downloads/{filename}`)

- **URL**: `/downloads/<filename>`
- **Method**: `GET`
- 支持直接下载生成的 `.mp3` 与 `.srt` 文件，内置路径穿越防护。

---

### 4. 服务健康状态 (GET `/health`)

- **URL**: `/health`
- **Method**: `GET`
- 返回服务的并发限制、重试阈值与配置目录状态。

---

## ⚙️ 环境变量配置参考

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `PORT` | `36485` | 映射到主机的服务端口 |
| `API_TOKEN` | 可选 | API 访问密钥。设置后所有调用需提供 `X-API-Token` |
| `OUTPUT_DIR` | `/nasShare/edgeTTSoutputs` | 音频和字幕文件的输出目录（不可写时自动降级到本地目录） |
| `FILE_LIFETIME_HOURS` | `192` | 文件保留时长（小时，默认 8 天 = 192 小时），后台定时清理过期文件 |
| `CLEANUP_INTERVAL_SECONDS`| `3600` | 后台清理任务运行周期（秒） |
| `TTS_MAX_CONCURRENCY` | `1` | 允许同时访问微软端点的最大并发数（防封核心配置） |
| `TTS_MAX_RETRIES` | `3` | 失败重试次数（1.5s, 3.5s 指数退避） |
| `TTS_TIMEOUT_SECONDS` | `300` | 任务基础超时时间（秒），长文会自动动态弹性放宽至最高 900 秒 |
| `TTS_INACTIVITY_TIMEOUT` | `60` | 流式输出静默超时（秒），只要文件持续增大即判定活跃不中断 |
| `TTS_COOLDOWN_SECONDS` | `0.5` | 请求处理后的防频控微冷却时间（秒） |
| `DEFAULT_PROXY` | 可选 | 默认全局代理（如遇服务器 IP 受限可配置） |
| `TZ` | `Asia/Shanghai` | 容器时区 |

---

## 📂 项目结构与复盘记录

```
.
├── app/
│   ├── main.py              # FastAPI 核心服务 (高健壮性防封队列、字幕重校准、API端点)
│   ├── requirements.txt     # 核心依赖清单
│   └── templates/
│       └── index.html       # 现代化一体 Web 语音合成工作台
├── docker-compose.yml       # Docker Compose 编排文件
├── Dockerfile               # 独立容器镜像构建脚本
├── tests/
│   └── test_core.py         # 核心逻辑单元测试
├── docs/
│   ├── CONVERSATION_HISTORY.md # 完整对话记录与复盘档案（原文留存）
│   └── archive/             # 历史旧文档与原始 HTML 原型存档
└── README.md                # 本文档
```

- 详细开发决策、用户 Prompt 原始记录与复盘分析详见：[docs/CONVERSATION_HISTORY.md](docs/CONVERSATION_HISTORY.md)。
- 历史旧文档与初始 HTML 页面归档详见：[docs/archive/](docs/archive/)。
