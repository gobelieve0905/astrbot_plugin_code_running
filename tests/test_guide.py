import re
import sys
import types
import unittest
from unittest.mock import patch

import guide


class GuideTests(unittest.TestCase):
    def example(self, section, client=None):
        scope = {}
        code = re.search(r"```python\n(.*?)```", guide.read_guide(section)["content"], re.S)[1]
        with patch.dict(
            sys.modules, {"controlled_api": client or types.ModuleType("controlled_api")}
        ):
            exec(compile(code, section, "exec"), scope)
        return scope

    def test_inventory_and_paths(self):
        for section in guide.SECTIONS:
            doc = guide.read_guide(section)
            self.assertTrue(doc["content"])
            self.assertEqual(len(doc["sha256"]), 64)
        for section in ("../main.py", "/etc/passwd", "references/batch.md"):
            with self.assertRaises(ValueError):
                guide.read_guide(section)
        with patch("pathlib.Path.is_symlink", return_value=True):
            with self.assertRaises(ValueError):
                guide.read_guide("overview")

    def test_manual_is_shipped_and_has_no_business_examples(self):
        for section in guide.SECTIONS:
            self.assertTrue(guide.read_guide(section)["content"])
        self.assertNotIn("run_segment", guide.read_guide("execution")["content"])
