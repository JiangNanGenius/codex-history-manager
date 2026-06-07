import json
import unittest

from media_adapters import (
    ADAPTER_ALIBABA_BAILIAN,
    ADAPTER_VOLCENGINE_ARK,
    build_media_adapter_preview,
    resolve_media_adapter_id,
    summarize_media_adapter_preview,
)


class MediaAdapterPreviewTest(unittest.TestCase):
    def test_resolves_adapter_from_provider_identity(self):
        self.assertEqual(
            resolve_media_adapter_id({"id": "volcengine-ark", "media_profile": {}}),
            ADAPTER_VOLCENGINE_ARK,
        )
        self.assertEqual(
            resolve_media_adapter_id({"kind": "alibaba_bailian", "media_profile": {}}),
            ADAPTER_ALIBABA_BAILIAN,
        )

    def test_volcengine_seedream_image_preview_is_metadata_only(self):
        preview = build_media_adapter_preview(
            {
                "id": "ark",
                "media_profile": {"adapter": "volcengine_ark"},
            },
            "image",
            "submit",
            model_id="ark/seedream",
            upstream_model_id="doubao-seedream-5-0-260128",
            request_json={"model": "seedream", "prompt": "private prompt", "size": "2K"},
        )

        self.assertEqual(preview["adapter_id"], ADAPTER_VOLCENGINE_ARK)
        self.assertEqual(preview["endpoint"]["path"], "/images/generations")
        self.assertFalse(preview["live_forwarding_enabled"])
        self.assertIn("prompt", preview["redacted_fields"])
        self.assertNotIn("private prompt", json.dumps(preview, ensure_ascii=False))

    def test_volcengine_seedance_video_preview_tracks_task_lifecycle(self):
        submit = build_media_adapter_preview({"id": "ark", "media_profile": {"adapter": "volcengine_ark"}}, "video", "submit")
        poll = build_media_adapter_preview({"id": "ark", "media_profile": {"adapter": "volcengine_ark"}}, "video", "poll")
        cancel = build_media_adapter_preview({"id": "ark", "media_profile": {"adapter": "volcengine_ark"}}, "video", "cancel")

        self.assertEqual(submit["endpoint"], {"method": "POST", "path": "/contents/generations/tasks"})
        self.assertEqual(poll["endpoint"], {"method": "GET", "path": "/contents/generations/tasks/{task_id}"})
        self.assertEqual(cancel["endpoint"], {"method": "DELETE", "path": "/contents/generations/tasks/{task_id}"})
        self.assertTrue(submit["async"])
        self.assertTrue(submit["poll_required"])
        self.assertTrue(submit["cancel_supported"])

    def test_bailian_qwen_image_preview_uses_dashscope_shape(self):
        preview = build_media_adapter_preview(
            {"id": "alibaba-bailian", "media_profile": {"adapter": "alibaba_bailian"}},
            "image",
            "submit",
        )

        self.assertEqual(preview["adapter_id"], ADAPTER_ALIBABA_BAILIAN)
        self.assertEqual(preview["endpoint"]["path"], "/api/v1/services/aigc/multimodal-generation/generation")
        self.assertIn("input.messages[0].content[].text", preview["request_shape"]["required"])
        self.assertEqual(preview["response_shape"]["image_url"], "output.choices[].message.content[].image")

    def test_summary_mentions_preview_only_block(self):
        preview = build_media_adapter_preview(
            {"id": "ark", "media_profile": {"adapter": "volcengine_ark"}},
            "video",
            "submit",
        )
        summary = summarize_media_adapter_preview(preview)

        self.assertIn("volcengine_ark", summary)
        self.assertIn("POST /contents/generations/tasks", summary)
        self.assertIn("Live vendor media conversion is not enabled yet", summary)


if __name__ == "__main__":
    unittest.main()
