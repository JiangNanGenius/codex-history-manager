import unittest

from media_proxy import (
    MEDIA_KIND_IMAGE,
    MEDIA_KIND_VIDEO,
    canonical_media_path,
    media_endpoint_url,
    media_forwarding_status,
    resolve_media_provider,
    resolve_media_route,
)


class MediaProxyHelperTest(unittest.TestCase):
    def test_canonical_media_path_strips_openai_prefixes(self):
        self.assertEqual(canonical_media_path("/v1/images/generations"), "/images/generations")
        self.assertEqual(canonical_media_path("/v1/v1/videos/video_1"), "/videos/video_1")
        self.assertEqual(canonical_media_path("/codex/v1/images/edits"), "/images/edits")

    def test_media_endpoint_url_adds_version_only_for_origin_base(self):
        self.assertEqual(
            media_endpoint_url("https://api.openai.com", "/images/generations"),
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(
            media_endpoint_url("https://api.openai.com/v1", "/images/generations"),
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(
            media_endpoint_url("https://media.example.test/openai", "/videos"),
            "https://media.example.test/openai/videos",
        )

    def test_resolve_default_media_provider_is_independent_from_text_provider(self):
        providers = [
            {
                "id": "text",
                "short_alias": "txt",
                "enabled": True,
                "capabilities": {"text": True, "images": False, "videos": False},
                "models": [{"id": "gpt-5", "enabled": True}],
            },
            {
                "id": "image",
                "short_alias": "img",
                "enabled": True,
                "capabilities": {"images": True},
                "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
                "models": [{"id": "gpt-image-1", "enabled": True, "capabilities": {"images": True}}],
            },
        ]

        provider = resolve_media_provider(providers, MEDIA_KIND_IMAGE, model_id="")
        self.assertEqual(provider["id"], "image")

    def test_provider_prefixed_media_model_hard_routes(self):
        providers = [
            {"id": "a", "short_alias": "a", "enabled": True, "capabilities": {"images": True}},
            {"id": "b", "short_alias": "b", "enabled": True, "capabilities": {"images": True}},
        ]

        provider = resolve_media_provider(providers, MEDIA_KIND_IMAGE, model_id="b/image-model")
        self.assertEqual(provider["id"], "b")

    def test_per_model_override_routes_to_media_provider_and_rewrites_model(self):
        providers = [
            {
                "id": "default-image",
                "short_alias": "default",
                "enabled": True,
                "capabilities": {"images": True},
                "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
            },
            {
                "id": "special-image",
                "short_alias": "special",
                "enabled": True,
                "capabilities": {"images": True},
                "media_profile": {
                    "openai_compatible_media": True,
                    "image_model_overrides": {"cover-art": "gpt-image-1.5"},
                },
            },
        ]

        route = resolve_media_route(providers, MEDIA_KIND_IMAGE, model_id="cover-art")

        self.assertEqual(route["provider"]["id"], "special-image")
        self.assertEqual(route["upstream_model_id"], "gpt-image-1.5")
        self.assertIn("Matched image model override", route["route_explanation"][0])

    def test_adapter_required_provider_is_blocked_for_pass_through(self):
        status = media_forwarding_status(
            {
                "id": "ark",
                "capabilities": {"videos": True},
                "media_profile": {"adapter_required": True, "openai_compatible_media": False},
            },
            MEDIA_KIND_VIDEO,
        )

        self.assertFalse(status["can_forward"])
        self.assertEqual(status["error_type"], "media_adapter_required")


if __name__ == "__main__":
    unittest.main()
