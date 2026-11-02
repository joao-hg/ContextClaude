import tempfile
import unittest
from pathlib import Path
from unittest import mock

from contextclaude.redact import redact


class TestRedact(unittest.TestCase):
    def test_secrets_removed(self):
        cases = [
            ("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA", "sk-ant"),
            ("ghp_" + "a" * 36, "ghp_aaaa"),
            ("AKIAABCDEFGHIJKLMNOP", "AKIAABCD"),
            ("Authorization: Bearer abc.def.ghi123", "abc.def"),
            ("password=hunter2", "hunter2"),
            ("-----BEGIN PRIVATE KEY-----\nXXXX\n-----END PRIVATE KEY-----", "XXXX"),
        ]
        for text, secret in cases:
            out = redact(text)
            self.assertNotIn(secret, out)
            self.assertIn("[REDACTED]", out)

    def test_plain_text_unchanged(self):
        self.assertEqual(redact("decidimos usar sqlite"), "decidimos usar sqlite")

    def test_redact_catches_high_entropy_token_without_known_prefix(self):
        text = "token aleatório: aK3mZ9qT7xP2wL5vN8bR1cF6hJ4dG0sU"
        self.assertIn("[REDACTED]", redact(text))
        self.assertNotIn("aK3mZ9qT7xP2wL5vN8bR1cF6hJ4dG0sU", redact(text))

    def test_redact_leaves_normal_prose_untouched(self):
        text = "isso é um teste normal de frase comum em português"
        self.assertEqual(redact(text), text)

    def test_redact_leaves_short_tokens_untouched(self):
        self.assertEqual(redact("id: abc123"), "id: abc123")

    def test_redact_applies_user_defined_pattern_from_file(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "redact_patterns.txt"
        p.write_text("PROJ-\\d{4,}\n", encoding="utf-8")
        with mock.patch("contextclaude.redact.config.redact_patterns_path", return_value=p):
            self.assertIn("[REDACTED]", redact("ticket interno PROJ-98123"))

    def test_redact_skips_invalid_pattern_line_without_crashing(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "redact_patterns.txt"
        p.write_text("[unclosed\nPROJ-\\d{4,}\n", encoding="utf-8")
        with mock.patch("contextclaude.redact.config.redact_patterns_path", return_value=p):
            self.assertIn("[REDACTED]", redact("PROJ-98123"))  # linha válida ainda funciona

    def test_redact_works_when_patterns_file_missing(self):
        with mock.patch("contextclaude.redact.config.redact_patterns_path", return_value=Path("/nao/existe/arquivo.txt")):
            self.assertEqual(redact("texto normal"), "texto normal")

    def test_redact_handles_unreadable_patterns_file_without_crashing(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "redact_patterns.txt"
        p.write_bytes(b"\xff\xfe\x00\x01PROJ-\xff\xff")  # bytes inválidos em UTF-8
        with mock.patch("contextclaude.redact.config.redact_patterns_path", return_value=p):
            self.assertEqual(redact("texto normal"), "texto normal")

    def test_redact_skips_pathological_pattern_line_without_crashing(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "redact_patterns.txt"
        p.write_text("a{99999999999}\nPROJ-\\d{4,}\n", encoding="utf-8")
        with mock.patch("contextclaude.redact.config.redact_patterns_path", return_value=p):
            out = redact("texto normal PROJ-98123")
            self.assertIn("[REDACTED]", out)
