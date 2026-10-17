import re
import unittest

URL_REGEX = re.compile(
    r"(?:(?:https?|ftp)://|www\.)[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)",
    re.IGNORECASE
)
EMOJI_REGEX = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u200d\ufe0f]"
)
IMAGE_TAG_REGEX = re.compile(r"\[image:\s[^\s.]+\.[1-9a-zA-Z]{3,4}\]")


def clean_text_for_tts(text: str) -> str:
    if not text:
        return ""
    if text.strip().startswith("<speak"):
        return text

    text = URL_REGEX.sub("", text)
    text = EMOJI_REGEX.sub("", text)
    text = re.sub(r"(?<!\d)67373(?!\d)", "六七三七三", text)
    text = text.replace("*", "")
    text = re.sub(r"[\\\/]", " ", text)
    text = text.replace("#", " ")
    text = IMAGE_TAG_REGEX.sub(" ", text)
    text = re.sub(r"-{2,}", " ", text)
    text = re.sub(r"={2,}", " ", text)

    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def srt_time_to_seconds(time_str: str) -> float:
    parts = time_str.split(',')
    h, m, s = map(int, parts[0].split(':'))
    ms = int(parts[1])
    return h * 3600 + m * 60 + s + ms / 1000.0

def seconds_to_srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h, remainder = divmod(seconds, 3600)
    m, remainder = divmod(remainder, 60)
    s, ms = divmod(remainder, 1)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(ms * 1000):03d}"

def resync_srt(srt_content: str, actual_duration: float) -> str:
    time_pattern = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})")
    matches = list(time_pattern.finditer(srt_content))
    if not matches:
        return srt_content
    original_end_time_str = matches[-1].group(2)
    original_duration = srt_time_to_seconds(original_end_time_str)
    if original_duration == 0:
        return srt_content
    scaling_factor = actual_duration / original_duration

    def replacer(match):
        start_time_str = match.group(1)
        end_time_str = match.group(2)
        new_start_secs = srt_time_to_seconds(start_time_str) * scaling_factor
        new_end_secs = srt_time_to_seconds(end_time_str) * scaling_factor
        return f"{seconds_to_srt_time(new_start_secs)} --> {seconds_to_srt_time(new_end_secs)}"

    return time_pattern.sub(replacer, srt_content)

def parse_srt_and_add_timestamps(original_text: str, srt_content: str) -> str:
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

    if last_pos < len(remaining_text):
        processed_parts.append(remaining_text[last_pos:])

    final_text = "".join(processed_parts)
    dup = "[time=00:00:00,000][time=00:00:00,000]"
    if final_text.startswith(dup):
        final_text = final_text[len("[time=00:00:00,000]"):]
    return final_text

def calculate_dynamic_timeout(text: str, base_timeout: int = 300) -> float:
    char_len = len(text) if text else 0
    return max(float(base_timeout), 300.0 + (char_len * 0.15))

class TestCoreLogic(unittest.TestCase):
    def test_time_conversions(self):
        secs = srt_time_to_seconds("00:01:23,456")
        self.assertAlmostEqual(secs, 83.456, places=3)
        formatted = seconds_to_srt_time(secs)
        self.assertEqual(formatted, "00:01:23,456")

    def test_resync_srt(self):
        srt_sample = "1\n00:00:00,000 --> 00:00:02,000\nHello World\n\n2\n00:00:02,000 --> 00:00:04,000\nSecond line\n"
        resynced = resync_srt(srt_sample, 6.0)
        self.assertIn("00:00:00,000 --> 00:00:03,000", resynced)
        self.assertIn("00:00:03,000 --> 00:00:06,000", resynced)

    def test_parse_srt_and_add_timestamps(self):
        text = "Hello World! This is a test.\nNewline sentence."
        srt_sample = "1\n00:00:00,500 --> 00:00:02,000\nHello World!\n\n2\n00:00:02,200 --> 00:00:03,500\nNewline sentence.\n"
        processed = parse_srt_and_add_timestamps(text, srt_sample)
        self.assertTrue(processed.startswith("[time=00:00:00,000]"))
        self.assertNotIn("[time=00:00:00,000][time=00:00:00,500]", processed)
        self.assertIn("[time=00:00:02,200]Newline sentence.", processed)

    def test_dynamic_timeout(self):
        short_timeout = calculate_dynamic_timeout("Short test")
        self.assertGreaterEqual(short_timeout, 300.0)
        long_text = "测" * 2000
        long_timeout = calculate_dynamic_timeout(long_text)
        self.assertGreaterEqual(long_timeout, 600.0) # 300 + 2000*0.15 = 600s (10 min)

    def test_safe_parse_rfc2616_date(self):
        from email.utils import parsedate_to_datetime
        from datetime import timezone
        date_str = "Mon, 12 Oct 2026 03:16:09 GMT"
        parsed = parsedate_to_datetime(date_str)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 10)
        self.assertEqual(parsed.day, 12)

    def test_clean_text_for_tts(self):
        # 1. 网址清理
        self.assertEqual(clean_text_for_tts("请访问 https://studio.67373.net/api/v1 谢谢"), "请访问 谢谢")
        # 2. Emoji 清理
        self.assertEqual(clean_text_for_tts("祝你开怀大笑 😀🎉 幸福"), "祝你开怀大笑 幸福")
        # 3. 67373 读作六七三七三 (前后非数字)
        self.assertEqual(clean_text_for_tts("欢迎使用 67373 服务"), "欢迎使用 六七三七三 服务")
        self.assertEqual(clean_text_for_tts("67373 置顶"), "六七三七三 置顶")
        self.assertEqual(clean_text_for_tts("电话13867373888不替换"), "电话13867373888不替换")
        # 4. Markdown 星号清理
        self.assertEqual(clean_text_for_tts("**加粗内容** 与 *斜体*"), "加粗内容 与 斜体")
        # 5. 斜杠替换为空格
        self.assertEqual(clean_text_for_tts("路径/a/b\\c 测试"), "路径 a b c 测试")
        # 6. 井号替换为空格
        self.assertEqual(clean_text_for_tts("#重要通知 #公告 内容"), "重要通知 公告 内容")
        # 7. 内联图片标记清理
        self.assertEqual(clean_text_for_tts("图片详情 [image: pic.png] 展示完毕"), "图片详情 展示完毕")
        # 8. 连续破折号与等号分割线清理
        self.assertEqual(clean_text_for_tts("标题\n---\n===\n正文"), "标题\n\n正文")
        # 9. SSML 保留
        ssml = "<speak><voice name='test'>Hello</voice></speak>"
        self.assertEqual(clean_text_for_tts(ssml), ssml)

if __name__ == '__main__':
    unittest.main()
