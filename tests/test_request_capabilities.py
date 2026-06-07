import unittest

from request_capabilities import classify_request_capabilities


class RequestCapabilityClassifierTest(unittest.TestCase):
    def test_responses_text_defaults_to_text(self):
        result = classify_request_capabilities("responses", {"model": "gpt-5", "input": "hello"})

        self.assertEqual(result["capabilities"], ["text"])
        self.assertTrue(result["signals"]["text"])

    def test_responses_input_image_requires_vision(self):
        result = classify_request_capabilities(
            "responses",
            {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "describe"},
                            {"type": "input_image", "image_url": "https://example.test/a.png"},
                        ],
                    }
                ]
            },
        )

        self.assertIn("text", result["capabilities"])
        self.assertIn("vision", result["capabilities"])
        self.assertTrue(result["signals"]["vision"])

    def test_chat_image_url_requires_vision(self):
        result = classify_request_capabilities(
            "chat_completions",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                        ],
                    }
                ]
            },
        )

        self.assertIn("vision", result["capabilities"])

    def test_tools_and_custom_tools_are_separate_signals(self):
        result = classify_request_capabilities(
            "responses",
            {
                "tools": [
                    {"type": "function", "name": "lookup"},
                    {"type": "custom", "name": "shell"},
                ]
            },
        )

        self.assertIn("tools", result["capabilities"])
        self.assertIn("custom_tools", result["capabilities"])
        self.assertTrue(result["signals"]["custom_tools"])

    def test_reasoning_and_compact_signals(self):
        result = classify_request_capabilities(
            "responses",
            {"reasoning": {"effort": "medium"}},
            compact=True,
        )

        self.assertIn("reasoning", result["capabilities"])
        self.assertTrue(result["signals"]["compact"])

    def test_media_endpoints_do_not_require_text_capability(self):
        image = classify_request_capabilities("images.generations", {"prompt": "cover"})
        video = classify_request_capabilities("videos", {"prompt": "clip"})

        self.assertEqual(image["capabilities"], ["images"])
        self.assertEqual(video["capabilities"], ["videos"])


if __name__ == "__main__":
    unittest.main()
