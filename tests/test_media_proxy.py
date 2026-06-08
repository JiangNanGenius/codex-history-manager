import unittest

from capabilities import normalize_capabilities
from media_proxy import (
    MEDIA_KIND_IMAGE,
    MEDIA_KIND_VIDEO,
    build_media_route_readiness,
    build_media_approval_action,
    canonical_media_path,
    evaluate_media_approval,
    media_endpoint_url,
    media_forwarding_status,
    media_operation_for_request,
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

    def test_media_model_match_uses_provider_images_with_legacy_model_caps(self):
        providers = [
            {
                "id": "text",
                "short_alias": "txt",
                "enabled": True,
                "capabilities": {"text": True, "images": False},
                "models": [{"id": "auto", "enabled": True, "capabilities": normalize_capabilities(None)}],
            },
            {
                "id": "image",
                "short_alias": "img",
                "enabled": True,
                "capabilities": {"text": True, "images": True},
                "media_profile": {"openai_compatible_media": True},
                "models": [{"id": "auto", "enabled": True, "capabilities": normalize_capabilities(None)}],
            },
        ]

        route = resolve_media_route(providers, MEDIA_KIND_IMAGE, model_id="auto")

        self.assertEqual(route["provider"]["id"], "image")

    def test_text_model_name_does_not_block_default_image_provider(self):
        providers = [
            {
                "id": "text",
                "short_alias": "txt",
                "enabled": True,
                "capabilities": {"text": True, "images": False},
                "models": [{"id": "gpt-5", "enabled": True}],
            },
            {
                "id": "image",
                "short_alias": "img",
                "enabled": True,
                "capabilities": {"images": True},
                "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
            },
        ]

        route = resolve_media_route(providers, MEDIA_KIND_IMAGE, model_id="gpt-5")

        self.assertEqual(route["provider"]["id"], "image")
        self.assertIn("Using default image provider", route["route_explanation"][0])

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
        self.assertEqual(status["adapter_preview"]["adapter_id"], "volcengine_ark")
        self.assertIn("/contents/generations/tasks", status["message"])

    def test_responses_provider_with_openai_compatible_media_forwards_images(self):
        status = media_forwarding_status(
            {
                "id": "mixed",
                "api_format": "openai_responses",
                "capabilities": {"images": True},
                "media_profile": {"openai_compatible_media": True},
            },
            MEDIA_KIND_IMAGE,
        )

        self.assertTrue(status["can_forward"])
        self.assertEqual(status["error_type"], "")

    def test_default_openai_compatible_image_provider_infers_support(self):
        status = media_forwarding_status(
            {
                "id": "native",
                "api_format": "openai_responses",
                "capabilities": {"text": True},
                "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
            },
            MEDIA_KIND_IMAGE,
        )

        self.assertTrue(status["can_forward"])
        self.assertEqual(status["error_type"], "")

    def test_media_route_readiness_shows_openai_image_upstream_url(self):
        readiness = build_media_route_readiness({
            "id": "native-image",
            "base_url": "https://api.example.test/v1",
            "api_format": "openai_responses",
            "capabilities": {"images": True},
            "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
        })

        image_check = next(item for item in readiness["checks"] if item["media_kind"] == MEDIA_KIND_IMAGE)
        video_check = next(item for item in readiness["checks"] if item["media_kind"] == MEDIA_KIND_VIDEO)
        self.assertTrue(readiness["live_forwarding_enabled"])
        self.assertTrue(image_check["can_forward"])
        self.assertEqual(image_check["upstream_url"], "https://api.example.test/v1/images/generations")
        self.assertIn("/v1/images/generations", image_check["proxy_paths"])
        self.assertFalse(video_check["can_forward"])
        self.assertEqual(video_check["error_type"], "media_capability_unsupported")

    def test_media_route_readiness_blocks_missing_base_url(self):
        readiness = build_media_route_readiness({
            "id": "native-image",
            "api_format": "openai_responses",
            "capabilities": {"images": True},
            "media_profile": {"openai_compatible_media": True},
        })

        image_check = next(item for item in readiness["checks"] if item["media_kind"] == MEDIA_KIND_IMAGE)
        self.assertFalse(readiness["live_forwarding_enabled"])
        self.assertFalse(image_check["can_forward"])
        self.assertEqual(image_check["error_type"], "media_base_url_missing")
        self.assertEqual(image_check["guidance_key"], "mediaBaseUrlNeededGuidance")
        self.assertEqual(image_check["action_key"], "mediaAddProviderUrlAction")

    def test_text_only_provider_readiness_guides_media_fallback(self):
        readiness = build_media_route_readiness({
            "id": "native-text",
            "api_format": "openai_responses",
            "capabilities": {"text": True, "images": False, "videos": False},
        })

        self.assertFalse(readiness["live_forwarding_enabled"])
        self.assertEqual(readiness["guidance_keys"], ["mediaTextProviderNeedsFallback"])
        self.assertEqual(readiness["action_keys"], ["mediaConfigureMediaFallbackAction"])
        for check in readiness["checks"]:
            self.assertFalse(check["can_forward"])
            self.assertEqual(check["error_type"], "media_capability_unsupported")
            self.assertEqual(check["guidance_key"], "mediaTextProviderNeedsFallback")
            self.assertEqual(check["action_key"], "mediaConfigureMediaFallbackAction")

    def test_native_responses_media_capability_guides_proxy_or_fallback(self):
        readiness = build_media_route_readiness({
            "id": "native-mixed",
            "base_url": "https://native.example.test/v1",
            "api_format": "openai_responses",
            "capabilities": {"text": True, "images": True, "videos": False},
        })

        image_check = next(item for item in readiness["checks"] if item["media_kind"] == MEDIA_KIND_IMAGE)
        video_check = next(item for item in readiness["checks"] if item["media_kind"] == MEDIA_KIND_VIDEO)
        self.assertFalse(readiness["live_forwarding_enabled"])
        self.assertIn("mediaNativeResponsesNeedsMediaProxy", readiness["guidance_keys"])
        self.assertIn("mediaCapabilityNeedsEnableOrFallback", readiness["guidance_keys"])
        self.assertFalse(image_check["can_forward"])
        self.assertEqual(image_check["error_type"], "media_adapter_required")
        self.assertEqual(image_check["guidance_key"], "mediaNativeResponsesNeedsMediaProxy")
        self.assertEqual(image_check["action_key"], "mediaConfirmNativeMediaProxyAction")
        self.assertEqual(video_check["guidance_key"], "mediaCapabilityNeedsEnableOrFallback")

    def test_media_route_readiness_exposes_adapter_required_blocker(self):
        readiness = build_media_route_readiness({
            "id": "volcengine-ark",
            "base_url": "https://ark.example.test/api/v3",
            "capabilities": {"images": True, "videos": True},
            "media_profile": {"adapter_required": True, "openai_compatible_media": False},
        })

        image_check = next(item for item in readiness["checks"] if item["media_kind"] == MEDIA_KIND_IMAGE)
        self.assertFalse(image_check["can_forward"])
        self.assertEqual(image_check["error_type"], "media_adapter_required")
        self.assertEqual(image_check["route_mode"], "adapter_required")
        self.assertEqual(image_check["adapter_preview"]["adapter_id"], "volcengine_ark")

    def test_media_operation_detects_submit_poll_cancel(self):
        self.assertEqual(media_operation_for_request("POST", "/v1/images/generations"), "submit")
        self.assertEqual(media_operation_for_request("POST", "/v1/videos"), "submit")
        self.assertEqual(media_operation_for_request("GET", "/v1/videos/video_1"), "poll")
        self.assertEqual(media_operation_for_request("DELETE", "/v1/videos/video_1"), "cancel")

    def test_media_approval_action_is_metadata_only(self):
        action = build_media_approval_action(
            {"id": "image-main"},
            MEDIA_KIND_IMAGE,
            "submit",
            "/images/generations",
            model_id="img/gpt-image-1",
            upstream_model_id="gpt-image-1",
            route_explanation=["Using default image provider."],
        )

        encoded = str(action)
        self.assertEqual(action["kind"], "image_generation")
        self.assertEqual(action["media"]["operation"], "submit")
        self.assertIn("gpt-image-1", encoded)
        self.assertNotIn("prompt", encoded.lower())

    def test_media_approval_not_required_when_profile_is_manual(self):
        result = evaluate_media_approval(
            {"id": "image-main", "approval_profile": {"mode": "manual_only"}},
            MEDIA_KIND_IMAGE,
            "submit",
            "/images/generations",
        )

        self.assertFalse(result["required"])
        self.assertTrue(result["approved"])

    def test_media_approval_default_mode_does_not_block_without_reviewer(self):
        result = evaluate_media_approval(
            {"id": "image-main"},
            MEDIA_KIND_IMAGE,
            "submit",
            "/images/generations",
        )

        self.assertTrue(result["required"])
        self.assertTrue(result["approved"])
        self.assertIn("implicit_default_no_reviewer", result["decision"]["policy_overrides"])

    def test_media_approval_uses_injected_reviewer(self):
        calls = []

        def reviewer(action, profile, provider):
            calls.append((action, profile, provider))
            return {"decision": "accept", "risk_level": "low", "reason": "Allowed media operation."}

        result = evaluate_media_approval(
            {"id": "video-main", "approval_profile": {"mode": "proxy_auto_approve"}},
            MEDIA_KIND_VIDEO,
            "cancel",
            "/videos/video_1",
            reviewer=reviewer,
        )

        self.assertTrue(result["required"])
        self.assertTrue(result["approved"])
        self.assertEqual(calls[0][0]["kind"], "video_generation")
        self.assertEqual(calls[0][0]["media"]["operation"], "cancel")

    def test_media_approval_without_reviewer_follows_error_policy(self):
        declined = evaluate_media_approval(
            {"id": "video-main", "approval_profile": {"mode": "proxy_auto_approve", "on_review_error": "decline"}},
            MEDIA_KIND_VIDEO,
            "submit",
            "/videos",
        )
        allowed = evaluate_media_approval(
            {"id": "video-main", "approval_profile": {"mode": "proxy_auto_approve", "on_review_error": "allow"}},
            MEDIA_KIND_VIDEO,
            "submit",
            "/videos",
        )

        self.assertFalse(declined["approved"])
        self.assertEqual(declined["error_type"], "media_auto_approval_declined")
        self.assertTrue(allowed["approved"])


if __name__ == "__main__":
    unittest.main()
