import copy
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

    def test_resume_no_repeat_or_missing_page(self):
        client = types.ModuleType("controlled_api")
        client.APIError = type("APIError", (Exception,), {})
        saved, calls = {}, []
        remaining = [1]
        pages = {
            ("a", None): ([1], True, "next"),
            ("a", "next"): ([2], False, None),
            ("b", None): ([3], False, None),
        }

        def call(tool, target, after):
            calls.append((target, after))
            remaining[0] -= 1
            return pages[target, after]

        client.call = call
        client.budget = lambda: {"remaining": remaining[0], "seconds_left": 600}
        client.checkpoint = lambda name, state: saved.update(copy.deepcopy(state))
        run = self.example("batch", client)["run_segment"]

        def build(target, cursor):
            return "read_only", {"target": target, "after": cursor}

        args = (["a", "b"], build, lambda response: response, "same-query")
        self.assertFalse(run(*args)["complete"])
        self.assertEqual(saved["cursor"], "next")
        remaining[0] = 2
        self.assertTrue(run(*args, saved=copy.deepcopy(saved))["complete"])
        self.assertEqual(calls, list(pages))
        self.assertEqual([r["row"] for r in saved["rows"]], [1, 2, 3])
        with self.assertRaises(ValueError):
            run(["a", "b"], build, lambda x: x, "changed-query", saved=saved)

    def test_permission_failure_and_invalid_cursor_not_complete(self):
        client = types.ModuleType("controlled_api")
        client.APIError = type("APIError", (Exception,), {})
        failure = client.APIError()
        failure.code = "PERMISSION_REVOKED"
        client.call = lambda *a, **k: (_ for _ in ()).throw(failure)
        client.budget = lambda: {"remaining": 10, "seconds_left": 600}
        client.checkpoint = lambda *a: None
        run = self.example("batch", client)["run_segment"]
        args = (["a"], lambda *a: ("read_only", {}), lambda x: x, "query")
        result = run(*args)
        self.assertFalse(result["complete"])
        self.assertEqual(result["failure"]["code"], "PERMISSION_REVOKED")
        client.call = lambda *a, **k: ([1], True, None)
        run = self.example("batch", client)["run_segment"]
        self.assertEqual(run(*args)["failure"]["code"], "INVALID_CURSOR")

    def test_aggregation_currency_weighting_and_duplicates(self):
        aggregate = self.example("analysis")["aggregate"]
        rows = [
            {
                "source": "a",
                "record_id": "1",
                "group": "x",
                "currency": "USD",
                "spend": "10",
                "purchases": 1,
            },
            {
                "source": "b",
                "record_id": "1",
                "group": "x",
                "currency": "USD",
                "spend": "30",
                "purchases": 3,
            },
            {
                "source": "c",
                "record_id": "1",
                "group": "x",
                "currency": "EUR",
                "spend": "20",
                "purchases": 0,
            },
        ]
        result = aggregate(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["cpa"], "10")
        self.assertIsNone(result[1]["cpa"])
        with self.assertRaises(ValueError):
            aggregate(rows + rows[:1])
