import json
import os
import tempfile
import unittest

from contextclaude.transcript import MAX_MSG, read_new


def ev(t, content):
    return json.dumps({"type": t, "message": {"role": t, "content": content}}, ensure_ascii=False)


class TestTranscript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *lines, mode="w"):
        with open(self.path, mode, encoding="utf-8", newline="\n") as f:
            for l in lines:
                f.write(l + "\n")

    def test_extracts_user_and_assistant_text(self):
        self.write(ev("user", "olá João"), ev("assistant", [{"type": "text", "text": "oi"}]))
        lines, off = read_new(self.path, 0)
        self.assertEqual(lines, ["Usuário: olá João", "Claude: oi"])
        self.assertEqual(off, os.path.getsize(self.path))

    def test_skips_tool_blocks_and_garbage_lines(self):
        self.write("not json", ev("assistant", [{"type": "tool_use", "name": "x"}]),
                   ev("user", [{"type": "tool_result", "content": "r"}]),
                   ev("user", "<system-reminder>ruído</system-reminder>"))
        self.assertEqual(read_new(self.path, 0)[0], [])

    def test_skips_injected_user_messages(self):
        self.write(ev("user", "Base directory for this skill: X\n# Skill"),
                   ev("user", "<task-notification>bg</task-notification>"), ev("user", "oi"))
        self.assertEqual(read_new(self.path, 0)[0], ["Usuário: oi"])

    def test_offset_resumes(self):
        self.write(ev("user", "um"))
        _, off = read_new(self.path, 0)
        self.write(ev("user", "dois"), mode="a")
        self.assertEqual(read_new(self.path, off)[0], ["Usuário: dois"])

    def test_partial_last_line_not_consumed(self):
        self.write(ev("user", "um"))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"type":"user","mess')
        lines, off = read_new(self.path, 0)
        self.assertEqual(lines, ["Usuário: um"])
        self.assertLess(off, os.path.getsize(self.path))

    def test_truncates_long_message(self):
        self.write(ev("user", "x" * (MAX_MSG + 500)))
        self.assertEqual(len(read_new(self.path, 0)[0][0]), len("Usuário: ") + MAX_MSG)

    def test_missing_file_returns_empty(self):
        self.assertEqual(read_new(os.path.join(self.tmp.name, "nada"), 7), ([], 7))
