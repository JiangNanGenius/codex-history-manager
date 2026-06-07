import unittest

from guardrails import (
    CODEX_MUTATION_CONFIRMATION,
    codex_mutation_error_payload,
    has_codex_mutation_confirmation,
)


class CodexMutationGuardrailTest(unittest.TestCase):
    def test_missing_confirmation_is_rejected(self):
        self.assertFalse(has_codex_mutation_confirmation({}))
        self.assertFalse(has_codex_mutation_confirmation({
            "manual_codex_mutation": True,
            "confirmation": "WRONG",
        }))

    def test_exact_confirmation_is_accepted(self):
        self.assertTrue(has_codex_mutation_confirmation({
            "manual_codex_mutation": True,
            "confirmation": CODEX_MUTATION_CONFIRMATION,
        }))

    def test_error_payload_is_action_specific(self):
        payload = codex_mutation_error_payload("restore_codex_auth")

        self.assertTrue(payload["manual_confirmation_required"])
        self.assertEqual(payload["required_confirmation"], CODEX_MUTATION_CONFIRMATION)
        self.assertEqual(payload["action"], "restore_codex_auth")


if __name__ == "__main__":
    unittest.main()
