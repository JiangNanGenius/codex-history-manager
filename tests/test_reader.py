import json
import tempfile
import unittest
from pathlib import Path

from reader import read_messages


class ReaderAttachmentTest(unittest.TestCase):
    def test_read_messages_preserves_file_and_image_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thread.jsonl"
            record = {
                "timestamp": "2026-01-01T00:00:00Z",
                "payload": {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "请看这些文件"},
                        {"type": "input_file", "filename": "requirements.txt", "file_id": "file_1"},
                        {"type": "input_image", "file_name": "screenshot.png", "file_id": "file_2"},
                    ],
                },
            }
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            result = read_messages(str(path))

            self.assertIsNone(result["error"])
            self.assertEqual(len(result["messages"]), 1)
            content = result["messages"][0]["content"]
            self.assertIn("请看这些文件", content)
            self.assertIn("[文件: requirements.txt]", content)
            self.assertIn("[图片: screenshot.png]", content)


if __name__ == "__main__":
    unittest.main()
