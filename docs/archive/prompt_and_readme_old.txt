有一个python的开源项目是这样介绍的：
安装方法：$ pip install edge-tts
使用方法：$ edge-tts --text "Hello, world!" --write-media hello.mp3 --write-subtitles hello.srt
使用说明：
edge-tts --help
usage: edge-tts [-h] [-t TEXT] [-f FILE] [-v VOICE] [-l] [--rate RATE] [--volume VOLUME] [--pitch PITCH] [--write-media WRITE_MEDIA] [--write-subtitles WRITE_SUBTITLES]
                [--proxy PROXY]

Text-to-speech using Microsoft Edge's online TTS service.

options:
  -h, --help            show this help message and exit
  -t, --text TEXT       what TTS will say
  -f, --file FILE       same as --text but read from file
  -v, --voice VOICE     voice for TTS. Default: en-US-EmmaMultilingualNeural
  -l, --list-voices     lists available voices and exits
  --rate RATE           set TTS rate. Default +0%.
  --volume VOLUME       set TTS volume. Default +0%.
  --pitch PITCH         set TTS pitch. Default +0Hz.
  --write-media WRITE_MEDIA
                        send media output to file instead of stdout
  --write-subtitles WRITE_SUBTITLES
                        send subtitle output to provided file instead of stderr
  --proxy PROXY         use a proxy for TTS and voice list.
请问我可以如何通过docker 使用这个项目，要求：
- 能通过网络请求进行调用，兼容voice，rate，volume，pitch，write media，subtitles，proxy参数，返回mp3和subtitle的下载地址，以及subtitle的内容的文本，需要可以指定文件名，并在重名时加编号。如果没有文件名，则取输入文字内容的前18个字符作为文件名，需要对特殊字符做处理。
mp3文件和subtitle文件保留88小时，临时文件通过docker目录映射出来，以便重启后还能使用。需要有一个通过环境变量配置的token以防止api被滥用。同时生成一份关于这个api的使用说明，说明采用尽量精简的文本格式。

docker通过docker-compose文件进行部署，需要将程序代码进行映射，这样就不用每次修改都要重新构建镜像。compose中不要构建镜像，而是通过手动构建镜像。



◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️


==============================
Edge-TTS API 简明使用说明
==============================

--- 核心功能 ---
通过POST请求发送文本，获取MP3音频和SRT字幕的下载链接。

--- 端点信息 ---
* 方法: POST
* URL: http://<your_server_ip>:<port>/tts
* 请求头: Content-Type: application/json

--- 请求参数 (JSON Body) ---
  - text (string, 必须): 要转换为语音的文本。
  - voice (string, 可选, 默认: "zh-CN-XiaoxiaoNeural"): 语音角色名称。
  - rate (string, 可选, 默认: "+0%"): 语速。示例: "+20%", "-10%"。
  - volume (string, 可选, 默认: "+0%"): 音量。示例: "+10%", "-20%"。
  - pitch (string, 可选, 默认: "+0Hz"): 音调。示例: "+50Hz", "-30Hz"。
  - proxy (string, 可选, 默认: null): 代理服务器。示例: "http://127.0.0.1:7890"。
  - filename 文件名

--- 调用示例 (curl) ---
curl -X POST "http://localhost:8000/tts" \
-H "Content-Type: application/json" \
-H "X-API-Token: your_super_secret_and_long_token_12345" \
-d '{
    "text": "这是一个简单的API调用。",
    "voice": "zh-CN-XiaoxiaoNeural",
    "filename": "my-cool-audio"
}'

--- 响应说明 ---
* 成功 (200 OK):
  返回一个JSON对象，包含 "media_url" 和 "subtitles_url"。
  {
    "message": "TTS generated successfully.",
    "media_url": "http://.../downloads/....mp3",
    "subtitles_url": "http://.../downloads/....srt",
    "expires_in_hours": 88
  }

* 错误 (422 / 500):
  - 422: 请求的JSON格式有误 (例如，末尾有逗号)。
  - 500: 后端处理失败 (例如，voice名称无效，文本超长)。

--- 重要事项 ---
1. 获取可用语音列表:
   在服务器终端运行: docker exec <容器名> edge-tts --list-voices

2. 文本长度限制:
   单次请求约4000字符。长文本需要由调用方自行分割。

3. 文件有效期:
   所有生成的文件会在服务器上保留88小时，之后自动删除。

4. 高级控制 (SSML):
   如果 "text" 字段内容以 "<speak>" 开头，则可以作为SSML处理，以控制停顿、语调等。
   
◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️◾️

###
curl -X POST "http://127.0.0.1:36485/tts" \
  -H "Content-Type: application/json" \
  -H "X-API-Token: your_secure_api_token_here" \
  -d '{
      "text": "你好，w\"[世界！欢迎使,用\n哇房这是一个有很多\n换行的\n句子\n\nafdsaf飒风由 Docker 部署的 edge-tts 服务【】。",
      "voice": "zh-CN-XiaoxiaoNeural",
      "filename": "my-cool-audio",
      "rate": "+0%",
      "pitch": "+0Hz"
  }'
  
###
curl -H "X-API-Token: your_secure_api_token_here" 'http://127.0.0.1:36485/list-files'

