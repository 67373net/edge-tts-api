FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PORT=80 \
    OUTPUT_DIR=/nasShare/edgeTTSoutputs \
    TTS_MAX_CONCURRENCY=1 \
    TTS_MAX_RETRIES=3 \
    TTS_TIMEOUT_SECONDS=300 \
    TTS_INACTIVITY_TIMEOUT=60 \
    TTS_COOLDOWN_SECONDS=0.5

# 支持配置构建时的 pip 镜像源（默认使用官方源并配置阿里云备选源，确保国内外均可稳定快速构建）
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /usr/src/app

# 安装依赖
COPY app/requirements.txt /usr/src/app/requirements.txt
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} --extra-index-url ${PIP_EXTRA_INDEX_URL} --upgrade pip && \
    pip install --no-cache-dir --index-url ${PIP_INDEX_URL} --extra-index-url ${PIP_EXTRA_INDEX_URL} --upgrade edge-tts && \
    pip install --no-cache-dir --index-url ${PIP_INDEX_URL} --extra-index-url ${PIP_EXTRA_INDEX_URL} -r requirements.txt

# 复制源码
COPY app/ /usr/src/app/

# 创建默认输出目录
RUN mkdir -p /nasShare/edgeTTSoutputs

EXPOSE 80

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
