import unittest

from official_quota import _extract_wham_quota_values, _read_codex_oauth


class OfficialQuotaTest(unittest.TestCase):
    def test_extract_wham_usage_windows_as_quota_tiers(self):
        values = _extract_wham_quota_values({
            "rate_limit": {
                "primary_window": {
                    "used_percent": 42.5,
                    "limit_window_seconds": 18000,
                    "reset_at": 1760000000,
                },
                "secondary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 604800,
                    "reset_at": 1760600000,
                },
            }
        })

        self.assertEqual(values["tool"], "codex_oauth")
        self.assertEqual(values["quota_percent"], 42.5)
        self.assertEqual(values["remaining_percent"], 57.5)
        self.assertEqual(values["tiers"][0]["name"], "five_hour")
        self.assertEqual(values["tiers"][1]["name"], "seven_day")

    def test_read_codex_oauth_requires_chatgpt_auth_mode(self):
        parsed = _read_codex_oauth({"auth_mode": "apikey", "tokens": {"access_token": "secret"}})

        self.assertEqual(parsed["credential_status"], "not_found")
        self.assertNotIn("access_token", parsed)

    def test_read_codex_oauth_extracts_token_and_account_id(self):
        parsed = _read_codex_oauth({
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": "secret",
                "account_id": "acct-1",
            },
        })

        self.assertEqual(parsed["credential_status"], "valid")
        self.assertEqual(parsed["access_token"], "secret")
        self.assertEqual(parsed["account_id"], "acct-1")


if __name__ == "__main__":
    unittest.main()
