import re
import unittest

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

if __name__ == '__main__':
    unittest.main()
