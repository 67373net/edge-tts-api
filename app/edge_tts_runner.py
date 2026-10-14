#!/usr/bin/env python3
"""
Edge-TTS 增强运行调度器 (Multi-Endpoint Auto-Failover & Sec-MS-GEC v2 Runner)

解决微软自 2026 年 10 月起对 Edge 语音合成服务强制启用 Sec-MS-GEC v2 签名，
以及替代端点 (api.msedgeservices.com) 产生 HTTP 429 (Too Many Requests) 频控的问题。

特性：
1. 完整兼容 Sec-MS-GEC v2 签名：
   - 在 edge-tts >= 7.3.1 环境下直接使用官方原生 v2 签名支持；
   - 在旧版环境 (如 7.2.8) 下自动无缝注入 v2 算法与版本头，无需重新构建容器镜像即可即刻恢复。
2. 双端点高可用热切换 (Failover)：
   - 首选微软官方主干端点 (speech.platform.bing.com)，承载能力强且杜绝 429 频控；
   - 备选端点 (api.msedgeservices.com) 自动容灾，遇到 403 或 429 握手异常时秒级平滑回退。
3. 跨 Locale 安全时间戳解析：
   - 避免非英语 Locale 下 strptime 解析 HTTP Date 异常导致的时钟漂移校准失败。
"""

import sys
from datetime import timezone as tz
from email.utils import parsedate_to_datetime
from aiohttp import WSServerHandshakeError

import edge_tts.constants
import edge_tts.communicate as communicate
from edge_tts.constants import TRUSTED_CLIENT_TOKEN, WSS_URL as LEGACY_WSS_URL
from edge_tts.drm import DRM
from edge_tts.util import main


# 1. 注入 Locale-Independent 日期安全解析
def safe_parse_rfc2616_date(date: str):
    try:
        parsed = parsedate_to_datetime(date)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz.utc)
    return parsed.timestamp()

DRM.parse_rfc2616_date = safe_parse_rfc2616_date


# 2. 检查并按需热补丁 Sec-MS-GEC v2 算法 (对 edge-tts < 7.3.1 的无缝向后兼容)
if not hasattr(edge_tts.drm, "generate_sec_ms_gec_v2"):
    try:
        from app.drm_v2 import generate_sec_ms_gec_v2
    except ImportError:
        try:
            from drm_v2 import generate_sec_ms_gec_v2
        except ImportError:
            generate_sec_ms_gec_v2 = None

    if generate_sec_ms_gec_v2 is not None:
        DRM.generate_sec_ms_gec = generate_sec_ms_gec_v2
        v2_version = f"2-{edge_tts.constants.CHROMIUM_FULL_VERSION}"
        edge_tts.constants.SEC_MS_GEC_VERSION = v2_version
        communicate.SEC_MS_GEC_VERSION = v2_version


# 3. 候选 WebSocket 端点（主干端点优先，备用端点容灾）
# 主干端点配合 v2 签名承载量最高且无 429 频控限制
WSS_URLS = [
    LEGACY_WSS_URL,
    f"wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}",
]


def run():
    last_err = None
    for i, url in enumerate(WSS_URLS):
        edge_tts.constants.WSS_URL = url
        communicate.WSS_URL = url
        try:
            main()
            return
        except WSServerHandshakeError as e:
            last_err = e
            # 当返回 403 (权限拒绝) 或 429 (频率受限) 且存在备选端点时，自动平滑切换
            if e.status not in (403, 429) or i == len(WSS_URLS) - 1:
                raise
            host = url.split("?")[0]
            print(
                f"[Edge-TTS-Runner] 端点 {host} 返回 HTTP {e.status}，正在自动切换至备用端点重试...",
                file=sys.stderr
            )
        except Exception as e:
            last_err = e
            if i == len(WSS_URLS) - 1:
                raise

    if last_err:
        raise last_err


if __name__ == "__main__":
    run()
