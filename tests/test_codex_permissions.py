import unittest

from codex_permissions import inspect_codex_permissions, preview_codex_permissions_update


class CodexPermissionsTest(unittest.TestCase):
    def test_detects_full_access_never_high_risk(self):
        result = inspect_codex_permissions({
            "approval_policy": "never",
            "sandbox_mode": "danger-full-access",
        })

        messages = [issue["message"] for issue in result["issues"]]
        self.assertTrue(result["effective_full_access"])
        self.assertTrue(any("Full access plus never-ask" in msg for msg in messages))

    def test_warns_on_deprecated_on_failure(self):
        result = inspect_codex_permissions({
            "approval_policy": "on-failure",
            "sandbox_mode": "workspace-write",
            "sandbox_workspace_write": {"network_access": True},
        })

        self.assertTrue(any(issue["field"] == "approval_policy" for issue in result["issues"]))
        self.assertIn("network_access=true", " ".join(result["warnings"]))

    def test_detects_missing_named_permission_profile(self):
        result = inspect_codex_permissions({
            "default_permissions": "dev",
            "permissions": {},
        })

        self.assertTrue(any("missing [permissions.dev]" in issue["message"] for issue in result["issues"]))

    def test_windows_sandbox_accepts_elevated_and_unelevated_only(self):
        valid = inspect_codex_permissions({"windows": {"sandbox": "unelevated"}})
        invalid = inspect_codex_permissions({"windows": {"sandbox": "restricted-token"}})

        self.assertEqual(valid["issue_count"], 0)
        self.assertTrue(any(issue["field"] == "windows.sandbox" for issue in invalid["issues"]))

    def test_preview_update_builds_diff_without_mutating_current(self):
        current = {
            "approval_policy": "on-request",
            "sandbox_mode": "read-only",
            "model": "gpt-5",
        }

        preview = preview_codex_permissions_update(
            current,
            approval_policy="never",
            sandbox_mode="workspace-write",
            sandbox_workspace_write={"network_access": True, "writable_roots": "C:/work"},
        )

        self.assertTrue(preview["will_write_config"])
        self.assertEqual(current["sandbox_mode"], "read-only")
        self.assertEqual(preview["config_diff"]["changed"]["sandbox_mode"]["new"], "workspace-write")
        self.assertEqual(preview["desired"]["sandbox_workspace_write"]["writable_roots"], ["C:/work"])


if __name__ == "__main__":
    unittest.main()
