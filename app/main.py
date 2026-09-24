import sys
import os
import uuid
import asyncio
import logging
import re
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, Header, status
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from starlette.staticfiles import StaticFiles
from starlette.responses import Response
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from mutagen.mp3 import MP3

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("edge-tts-service")

# --- 环境与配置 ---
API_TOKEN = os.getenv("API_TOKEN")
FILE_LIFETIME_HOURS = int(os.getenv("FILE_LIFETIME_HOURS", "192")) # 默认保留 8 天 (192 小时)
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))

# 防频控与健壮性配置
TTS_MAX_CONCURRENCY = int(os.getenv("TTS_MAX_CONCURRENCY", "1")) # 限制同时访问微软端点的并发数，防封IP
TTS_MAX_RETRIES = int(os.getenv("TTS_MAX_RETRIES", "3"))         # 单次失败最大重试次数
TTS_TIMEOUT_SECONDS = int(os.getenv("TTS_TIMEOUT_SECONDS", "300")) # 基础任务时限（秒，默认提升至300s/5分钟，长文动态上浮）
TTS_INACTIVITY_TIMEOUT = int(os.getenv("TTS_INACTIVITY_TIMEOUT", "60")) # 流式数据静默假死超时（秒）
TTS_COOLDOWN_SECONDS = float(os.getenv("TTS_COOLDOWN_SECONDS", "0.5")) # 请求后平滑冷却时间
DEFAULT_PROXY = os.getenv("DEFAULT_PROXY", None)

# 并发限制信号量
tts_semaphore = asyncio.Semaphore(TTS_MAX_CONCURRENCY)

# 输出目录探测与自动回退
CONFIGURED_OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/nasShare/edgeTTSoutputs")
try:
    os.makedirs(CONFIGURED_OUTPUT_DIR, exist_ok=True)
    test_probe = os.path.join(CONFIGURED_OUTPUT_DIR, f".probe_{uuid.uuid4().hex[:6]}")
    with open(test_probe, "w") as f:
        f.write("probe")
    os.remove(test_probe)
    OUTPUT_DIR = CONFIGURED_OUTPUT_DIR
except Exception as err:
    fallback_dir = str(Path(__file__).resolve().parent.parent / "nasShare" / "edgeTTSoutputs")
    os.makedirs(fallback_dir, exist_ok=True)
    logger.warning(f"配置的输出路径 '{CONFIGURED_OUTPUT_DIR}' 不可写 ({err})，自动降级为本地目录: '{fallback_dir}'")
    OUTPUT_DIR = fallback_dir

ABSOLUTE_OUTPUT_DIR = Path(OUTPUT_DIR).resolve()
logger.info(f"最终生效的文件存储目录: {ABSOLUTE_OUTPUT_DIR}")


# --- 后台过期文件清理任务 ---
async def cleanup_old_files():
    """
    后台常驻定时任务：使用高性能 scandir 扫描并清理超过保留时限的文件
    """
    while True:
        try:
            now = datetime.now(timezone.utc)
            lifetime_delta = timedelta(hours=FILE_LIFETIME_HOURS)
            removed_count = 0
            if os.path.exists(OUTPUT_DIR):
                with os.scandir(OUTPUT_DIR) as entries:
                    for entry in entries:
                        if entry.name.startswith('.'):
                            continue
                        try:
                            if entry.is_file():
                                st = entry.stat()
                                mod_time_utc = datetime.fromtimestamp(st.st_mtime, timezone.utc)
                                if now - mod_time_utc > lifetime_delta:
                                    os.remove(entry.path)
                                    removed_count += 1
                                    logger.info(f"已清理过期生成文件: {entry.name}")
                        except OSError:
                            pass
            if removed_count > 0:
                logger.info(f"本次自动清理完成，共删除 {removed_count} 个过期文件。")
        except Exception as e:
            logger.error(f"后台文件清理任务执行异常: {e}", exc_info=True)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


# --- 现代 FastAPI 生命周期管理器 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Edge-TTS 服务正在启动...")
    logger.info(f"并发队列上限: {TTS_MAX_CONCURRENCY}, 最大重试: {TTS_MAX_RETRIES}, 超时: {TTS_TIMEOUT_SECONDS}s")
    if API_TOKEN:
        logger.info("服务已启用 API Token 鉴权保护。")
    else:
        logger.warning("警告: 未设置 API_TOKEN，服务当前工作在免认证模式下。")
    
    # 启动后台清理任务
    cleanup_task = asyncio.create_task(cleanup_old_files())
    yield
    # 关闭时取消任务
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Edge-TTS 服务已停止。")


# --- FastAPI 实例 ---
app = FastAPI(
    title="Edge-TTS API with Subtitle Resync & Web Workbench",
    description="高健壮性 Microsoft Edge-TTS 语音合成服务，内置防频控排队、自动重试、字幕线性校准与一体化工作台。",
    version="3.0.0",
    lifespan=lifespan
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip 压缩：大幅减少 HTML/JSON/SRT 等文本传输体积与网络耗时
app.add_middleware(GZipMiddleware, minimum_size=500)


# 高性能静态资源挂载（带客户端强缓存响应头）
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            # 试听音频等静态资产 7 天强缓存 + 标记 immutable，大幅降低重复试听请求开销
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return response


STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")


# --- 数据模型（严格保持不变，完全向后兼容） ---
class TTSRequest(BaseModel):
    text: str = Field(..., title="要转换的文本")
    voice: str = Field("zh-CN-XiaoxiaoNeural", title="语音角色")
    rate: str = Field("+0%", title="语速")
    volume: str = Field("+0%", title="音量")
    pitch: str = Field("+0Hz", title="音调")
    proxy: str | None = Field(None, title="代理服务器")
    filename: str | None = Field(None, title="指定输出文件名(不含扩展名)")


# --- Token 验证依赖 ---
async def verify_token(
    x_api_token: str | None = Header(None, alias="X-API-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
    token: str | None = None
):
    if not API_TOKEN:
        return
    provided = x_api_token
    if not provided and authorization:
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        else:
            provided = authorization.strip()
    if not provided and token:
        provided = token

    if not provided or provided != API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无效或缺失的 API Token"
        )


# --- SRT 与时间轴校准工具函数 ---
def srt_time_to_seconds(time_str: str) -> float:
    """将 SRT 时间戳字符串 (HH:MM:SS,ms) 转换为总秒数"""
    parts = time_str.split(',')
    h, m, s = map(int, parts[0].split(':'))
    ms = int(parts[1])
    return h * 3600 + m * 60 + s + ms / 1000.0


def seconds_to_srt_time(seconds: float) -> str:
    """将总秒数转换为 SRT 时间戳字符串 (HH:MM:SS,ms)"""
    if seconds < 0:
        seconds = 0
    h, remainder = divmod(seconds, 3600)
    m, remainder = divmod(remainder, 60)
    s, ms = divmod(remainder, 1)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(ms * 1000):03d}"


def resync_srt(srt_content: str, actual_duration: float) -> str:
    """
    根据实际音频时长，线性缩放 SRT 字幕文件中的所有时间戳。
    """
    time_pattern = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})")
    matches = list(time_pattern.finditer(srt_content))
    if not matches:
        logger.warning("SRT 内容中未找到时间戳，无法执行重同步。")
        return srt_content

    original_end_time_str = matches[-1].group(2)
    original_duration = srt_time_to_seconds(original_end_time_str)

    if original_duration == 0:
        logger.warning("SRT 原始时长为 0，无法计算缩放比例。")
        return srt_content

    scaling_factor = actual_duration / original_duration
    logger.info(f"字幕重同步：实际时长={actual_duration:.3f}s, SRT原始时长={original_duration:.3f}s, 缩放比例={scaling_factor:.4f}")

    def replacer(match):
        start_time_str = match.group(1)
        end_time_str = match.group(2)

        original_start_secs = srt_time_to_seconds(start_time_str)
        original_end_secs = srt_time_to_seconds(end_time_str)

        new_start_secs = original_start_secs * scaling_factor
        new_end_secs = original_end_secs * scaling_factor

        new_start_str = seconds_to_srt_time(new_start_secs)
        new_end_str = seconds_to_srt_time(new_end_secs)

        return f"{new_start_str} --> {new_end_str}"

    return time_pattern.sub(replacer, srt_content)


def parse_srt_and_add_timestamps(original_text: str, srt_content: str) -> str:
    """
    根据 srt 字幕，在原文稿中加上时间戳 [time=xx:xx:xx,xxx]
    """
    srt_pattern = re.compile(r'\d+\n([\d:,]+) --> [\d:,]+\n([\s\S]+?)(?=\n\n|\Z)', re.MULTILINE)
    matches = srt_pattern.findall(srt_content)
    if not matches:
        return f"[time=00:00:00,000]{original_text}"

    processed_parts = ["[time=00:00:00,000]"]
    remaining_text = original_text
    last_pos = 0

    for idx, (start_time, subtitle_text) in enumerate(matches):
        search_text = subtitle_text.strip().replace('\r\n', '\n')
        pos = remaining_text.find(search_text, last_pos)
        if pos != -1:
            processed_parts.append(remaining_text[last_pos:pos])
            if idx > 0:
                processed_parts.append(f"[time={start_time}]")
            processed_parts.append(search_text)
            last_pos = pos + len(search_text)
        else:
            logger.debug(f"未在剩余文稿中找到字幕: '{search_text}'")

    if last_pos < len(remaining_text):
        processed_parts.append(remaining_text[last_pos:])

    final_text = "".join(processed_parts)
    duplicate_prefix = "[time=00:00:00,000][time=00:00:00,000]"
    if final_text.startswith(duplicate_prefix):
        final_text = final_text[len("[time=00:00:00,000]"):]

    return final_text


# --- 动态任务时限与流式活动守护看门狗 ---
def calculate_dynamic_timeout(text: str) -> float:
    """
    根据文稿长度动态计算任务时限：
    基础保底 300 秒（5分钟），长文稿每 100 字符额外给予 15 秒缓冲，最高可放宽至 900 秒（15 分钟）
    """
    char_len = len(text) if text else 0
    return max(float(TTS_TIMEOUT_SECONDS), 300.0 + (char_len * 0.15))


async def wait_process_with_stream_watchdog(
    process: asyncio.subprocess.Process,
    mp3_filepath: str,
    max_timeout: float,
    inactivity_timeout: float
) -> tuple[bytes, bytes]:
    """
    带流式写入活跃检测的守护等待：
    只要 MP3 文件大小在持续增长（说明正顺畅流式接收微软音频数据），任务就不中断；
    仅在无数据流入超过 inactivity_timeout（假死）或达到 max_timeout（绝对上限）时触发熔断。
    """
    comm_task = asyncio.create_task(process.communicate())
    start_time = asyncio.get_event_loop().time()
    last_active_time = start_time
    last_size = -1

    while not comm_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(comm_task), timeout=1.0)
            break
        except asyncio.TimeoutError:
            pass

        now = asyncio.get_event_loop().time()
        # 探测输出文件大小是否在增长（流式数据流入）
        if os.path.exists(mp3_filepath):
            try:
                current_size = os.path.getsize(mp3_filepath)
                if current_size > last_size:
                    last_size = current_size
                    last_active_time = now  # 刷新活跃时间
            except OSError:
                pass

        # 1. 检查总耗时是否超过该文本的动态最大时限
        if (now - start_time) > max_timeout:
            comm_task.cancel()
            raise asyncio.TimeoutError(f"任务总执行时间超过上限 ({int(max_timeout)}s)")

        # 2. 检查静默时间（无新数据流入）是否超过假死阈值
        if (now - last_active_time) > inactivity_timeout:
            comm_task.cancel()
            raise asyncio.TimeoutError(f"连接假死/无数据流入超过静默时限 ({int(inactivity_timeout)}s)")

    stdout, stderr = await comm_task
    return stdout, stderr


# --- 健壮执行 Edge-TTS 命令（防封队列 + 活跃流守护 + 重试） ---
async def execute_edge_tts_with_retry(
    command: list[str],
    mp3_filepath: str,
    srt_filepath: str,
    text: str = ""
) -> None:
    """
    在信号量队列中执行 edge-tts，支持流式活跃守护、超时打断、指数退避重试和临时文件清理
    """
    dynamic_timeout = calculate_dynamic_timeout(text)
    last_error = ""

    for attempt in range(1, TTS_MAX_RETRIES + 1):
        logger.info(
            f"[TTS-Queue] 开始执行生成任务 (尝试 {attempt}/{TTS_MAX_RETRIES}, "
            f"动态时限: {int(dynamic_timeout)}s, 静默超时: {TTS_INACTIVITY_TIMEOUT}s)..."
        )
        process = None
        try:
            # 清理历史可能残留的半成品文件
            for fp in [mp3_filepath, srt_filepath]:
                if os.path.exists(fp):
                    try: os.remove(fp)
                    except OSError: pass

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await wait_process_with_stream_watchdog(
                process,
                mp3_filepath,
                max_timeout=dynamic_timeout,
                inactivity_timeout=float(TTS_INACTIVITY_TIMEOUT)
            )

            if process.returncode == 0:
                # 检查输出文件是否有效生成
                if os.path.exists(mp3_filepath) and os.path.getsize(mp3_filepath) > 0:
                    logger.info(f"[TTS-Queue] 任务成功完成 (尝试 {attempt})。")
                    if TTS_COOLDOWN_SECONDS > 0:
                        await asyncio.sleep(TTS_COOLDOWN_SECONDS)
                    return
                else:
                    raise RuntimeError("TTS 执行完毕但未找到生成的有效 MP3 音频文件")

            error_msg = stderr.decode('utf-8', errors='ignore').strip() if stderr else "未知错误"
            last_error = f"Edge-TTS 进程返回非零状态码 ({process.returncode}): {error_msg}"
            logger.warning(f"[TTS-Queue] 尝试 {attempt} 失败: {last_error}")

        except asyncio.TimeoutError as te:
            last_error = f"Edge-TTS 请求超时/假死: {te}"
            logger.warning(f"[TTS-Queue] 尝试 {attempt} 超时: {last_error}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[TTS-Queue] 尝试 {attempt} 异常: {last_error}")
        finally:
            if process and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass

        # 如果未达到最大尝试次数，则进行指数退避等待
        if attempt < TTS_MAX_RETRIES:
            backoff_delay = (1.5 * (2 ** (attempt - 1))) + random.uniform(0.2, 0.8)
            logger.info(f"[TTS-Queue] 等待 {backoff_delay:.2f} 秒后重试...")
            await asyncio.sleep(backoff_delay)

    # 全部尝试均失败
    raise HTTPException(
        status_code=500,
        detail=f"Edge-TTS 语音生成失败 (已重试 {TTS_MAX_RETRIES} 次)。详情: {last_error}"
    )


# --- 路由：服务状态健康检查 ---
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": "3.1.0",
        "concurrency_limit": TTS_MAX_CONCURRENCY,
        "max_retries": TTS_MAX_RETRIES,
        "base_timeout_seconds": TTS_TIMEOUT_SECONDS,
        "inactivity_timeout_seconds": TTS_INACTIVITY_TIMEOUT,
        "token_protected": bool(API_TOKEN),
        "output_dir": str(ABSOLUTE_OUTPUT_DIR)
    }


# --- 路由：提供内置 Web 工作台页面 ---
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "index.html"
_CACHED_WORKBENCH_HTML: str | None = None

def get_cached_workbench_html() -> str:
    global _CACHED_WORKBENCH_HTML
    if _CACHED_WORKBENCH_HTML is None:
        if TEMPLATE_PATH.exists():
            with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            token_to_inject = API_TOKEN or ""
            _CACHED_WORKBENCH_HTML = content.replace("__ENV_API_TOKEN__", token_to_inject)
        else:
            _CACHED_WORKBENCH_HTML = "<h2>Edge-TTS API 运行中</h2><p>模板文件未找到，请检查 app/templates/index.html 是否存在。</p>"
    return _CACHED_WORKBENCH_HTML


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
@app.api_route("/ui", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_workbench():
    return HTMLResponse(content=get_cached_workbench_html(), status_code=200)


# --- 路由：安全文件下载 (支持 GET 与 HEAD 请求) ---
@app.api_route("/downloads/{filename:path}", methods=["GET", "HEAD"])
async def download_file(filename: str):
    # 安全校验：防止路径穿越
    target_path = (ABSOLUTE_OUTPUT_DIR / filename).resolve()
    if not target_path.is_relative_to(ABSOLUTE_OUTPUT_DIR) or not target_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已过期删除")

    media_type = "audio/mpeg" if filename.lower().endswith(".mp3") else "text/plain; charset=utf-8"
    response = FileResponse(path=str(target_path), filename=filename, media_type=media_type)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# --- 路由：获取最近生成的文件列表 ---
@app.get("/list-files", dependencies=[Depends(verify_token)])
async def list_files(request: Request):
    files_info = []
    base_url = str(request.base_url)
    try:
        if os.path.exists(OUTPUT_DIR):
            with os.scandir(OUTPUT_DIR) as entries:
                for entry in entries:
                    if entry.name.startswith('.'):
                        continue
                    try:
                        if entry.is_file():
                            st = entry.stat()
                            mod_time_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                            files_info.append({
                                "filename": entry.name,
                                "size": st.st_size,
                                "modified_at": mod_time_iso,
                                "url": f"{base_url}downloads/{entry.name}"
                            })
                    except OSError:
                        pass
        files_info.sort(key=lambda x: x['modified_at'], reverse=True)
        return files_info
    except Exception as e:
        logger.error(f"列出历史文件出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取文件列表失败")


# --- 核心路由：文本转语音 TTS (严格保持原有参数与返回契约) ---
@app.post("/tts", response_class=JSONResponse, dependencies=[Depends(verify_token)])
async def create_tts(request: Request, tts_request: TTSRequest):
    # 1. 安全计算文件名
    base_name_suggestion = ""
    if tts_request.filename:
        base_name_suggestion = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fa5]', '', tts_request.filename).strip()
    else:
        sane_text = re.sub(r'[^\w\u4e00-\u9fa5\-]', '', tts_request.text)
        text_part = sane_text[:18]
        voice_part = tts_request.voice or "XiaoxiaoNeural"
        sanitized_voice = re.sub(r'[^a-zA-Z0-9_\-]', '', voice_part)
        base_name_suggestion = f"{text_part}_{sanitized_voice}"

    if not base_name_suggestion or base_name_suggestion.startswith('_'):
        base_name_suggestion = uuid.uuid4().hex

    # 2. 避免重名覆盖：自动递增后缀
    base_path = os.path.join(OUTPUT_DIR, base_name_suggestion)
    mp3_path_to_check = f"{base_path}.mp3"
    counter = 1
    final_base_name = base_name_suggestion
    while os.path.exists(mp3_path_to_check):
        final_base_name = f"{base_name_suggestion}_{counter}"
        mp3_path_to_check = os.path.join(OUTPUT_DIR, f"{final_base_name}.mp3")
        counter += 1

    mp3_filename = f"{final_base_name}.mp3"
    srt_filename = f"{final_base_name}.srt"
    mp3_filepath = os.path.join(OUTPUT_DIR, mp3_filename)
    srt_filepath = os.path.join(OUTPUT_DIR, srt_filename)

    # 3. 组装 Edge-TTS 执行命令
    command = [
        sys.executable, "-m", "edge_tts",
        "--text", tts_request.text,
        "--voice", tts_request.voice,
        "--rate", tts_request.rate,
        "--volume", tts_request.volume,
        "--pitch", tts_request.pitch,
        "--write-media", mp3_filepath,
        "--write-subtitles", srt_filepath,
    ]

    effective_proxy = tts_request.proxy or DEFAULT_PROXY
    if effective_proxy:
        command.extend(["--proxy", effective_proxy])

    # 4. 进入并发防封队列，带流式活跃守护与动态超时重试执行
    async with tts_semaphore:
        await execute_edge_tts_with_retry(command, mp3_filepath, srt_filepath, text=tts_request.text)

    # 5. 字幕线性重同步（根据 MP3 实际长度拉伸/压缩时间轴）
    srt_content = ""
    try:
        if os.path.exists(srt_filepath):
            with open(srt_filepath, 'r', encoding='utf-8') as f:
                srt_content = f.read()

            # 获取 MP3 实际播放时长
            try:
                audio = MP3(mp3_filepath)
                actual_duration = audio.info.length
                if actual_duration > 0:
                    resynced_srt = resync_srt(srt_content, actual_duration)
                    with open(srt_filepath, 'w', encoding='utf-8') as f:
                        f.write(resynced_srt)
                    srt_content = resynced_srt
                    logger.info(f"成功重同步 SRT 文件: {srt_filename}")
            except Exception as mp3_err:
                logger.warning(f"无法重同步 SRT 文件 {srt_filename} (保留原始字幕): {mp3_err}")
        else:
            logger.warning(f"SRT 文件未生成: {srt_filepath}")
    except Exception as read_err:
        logger.error(f"处理 SRT 文件时出错: {read_err}", exc_info=True)

    # 6. 在原文稿中打上时间戳
    processed_text = parse_srt_and_add_timestamps(tts_request.text, srt_content)
    base_url = str(request.base_url)

    # 7. 返回完全兼容的结果结构
    return {
        "message": "TTS generated successfully.",
        "media_url": f"{base_url}downloads/{mp3_filename}",
        "subtitles_url": f"{base_url}downloads/{srt_filename}",
        "subtitles_content": srt_content,
        "processed_text": processed_text
    }
