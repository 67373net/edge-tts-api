#!/usr/bin/env python3
"""
Edge-TTS 增强运行调度器 (Multi-Endpoint Auto-Failover Runner)

解决微软自 2026 年 10 月起对 speech.platform.bing.com 原旧版 WebSocket 端点
返回 HTTP 403 (Invalid response status) 的全球封禁/变更问题。

特性：
1. 优先使用微软最新合成端点 (api.msedgeservices.com)，旧版端点作为容灾回退。
2. 修复多语言环境下的 HTTP Date 解析问题，确保时间校准与时钟偏移 (Clock Skew) 算法在任何 Locale 下均正常工作。
3. 保持与 edge-tts 命令行参数 100% 兼容。
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


def safe_parse_rfc2616_date(date: str):
    """
    无视系统 Locale 安全解析 HTTP RFC 2616 时间戳，解决非英文环境下 strptime 报错导致时钟偏移校准失败
    """
    try:
        parsed = parsedate_to_datetime(date)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz.utc)
    return parsed.timestamp()


# 注入时间戳安全解析
DRM.parse_rfc2616_date = safe_parse_rfc2616_date

# 候选 WebSocket 合成端点列表（最新高可用端点优先，旧版端点备用）
WSS_URLS = [
    f"wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}",
    LEGACY_WSS_URL,
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
            if e.status != 403 or i == len(WSS_URLS) - 1:
                raise
            host = url.split("?")[0]
            print(f"[Edge-TTS-Runner] 端点 {host} 返回 403，正在自动切换至备用端点重试...", file=sys.stderr)
        except Exception as e:
            last_err = e
            if i == len(WSS_URLS) - 1:
                raise
    if last_err:
        raise last_err


if __name__ == "__main__":
    run()
