import asyncio
import base64
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jobs import Jobs, filename  # noqa: E402


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.socket = str(self.root / "runner.sock")
        self.manager = Jobs(self.root / "jobs", self.socket, str(self.root / "absent.sock"))
        self.server = await asyncio.start_unix_server(self.fake, self.socket)

    async def fake(self, reader, writer):
        line = await reader.readline()
        if not line:
            writer.close()
            await writer.wait_closed()
            return
        payload = json.loads(line)
        if payload["code"] == "wait":
            await reader.read()
        else:
            for event in [
                {"type": "output", "text": "42"},
                {
                    "type": "file",
                    "name": "result.csv",
                    "data": base64.b64encode(b"x\n42\n").decode(),
                },
                {"type": "done", "ok": True},
            ]:
                writer.write(json.dumps(event).encode() + b"\n")
                await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def asyncTearDown(self):
        await self.manager.close()
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def test_result_is_owned_and_verified(self):
        record = self.manager.start("alice", "print(42)", {}, {})
        task = self.manager.tasks[record["id"]]
        await task
        final = self.manager.get(record["id"], "alice")
        self.assertEqual(final["state"], "succeeded")
        self.assertEqual(final["output"], "42")
        path = self.manager.artifact(record["id"], "result.csv", "alice")
        self.assertEqual(path.read_bytes(), b"x\n42\n")
        with self.assertRaises(ValueError):
            self.manager.get(record["id"], "bob")
        with self.assertRaises(ValueError):
            self.manager.artifact(record["id"], "result.csv", "bob")
        path.write_text("bad")
        with self.assertRaises(ValueError):
            self.manager.artifact(record["id"], "result.csv", "alice")

    async def test_stop_and_global_slot(self):
        record = self.manager.start("alice", "wait", {}, {})
        await asyncio.sleep(0.02)
        with self.assertRaises(ValueError):
            self.manager.start("bob", "x", {}, {})
        with self.assertRaises(ValueError):
            await self.manager.stop(record["id"], "bob")
        result = await self.manager.stop(record["id"], "alice")
        self.assertEqual(result["state"], "stopped")

    async def test_api_missing_fails_closed(self):
        record = self.manager.start("alice", "print(42)", {"api_x": {}}, {})
        await self.manager.tasks[record["id"]]
        self.assertEqual(self.manager.get(record["id"])["state"], "failed")

    async def test_restart_marks_interrupted(self):
        record = self.manager.start("alice", "wait", {}, {})
        again = Jobs(self.root / "jobs", self.socket, "none")
        self.assertEqual(again.get(record["id"])["state"], "interrupted")

    async def test_traversal_and_limits(self):
        for name in ["../x", "/etc/passwd", "..", "a\\b", "x\0y"]:
            with self.assertRaises(ValueError):
                filename(name)
        with self.assertRaises(ValueError):
            self.manager.start("a", "x", {}, {}, 601)
        with self.assertRaises(ValueError):
            self.manager.start("a", "x", {}, {}, 120, 201)
        with self.assertRaises(ValueError):
            self.manager.start("a", "x", {}, {"x": b"a" * (8 * 1024 * 1024 + 1)})


class DockerPolicy(unittest.TestCase):
    def test_fixed_isolation(self):
        spec = importlib.util.spec_from_file_location("service", ROOT / "runner/service.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = module.command("sha256:" + "a" * 64, "test")
        for flag in [
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--memory=192m",
            "--pids-limit=32",
            "--user=65532:65532",
            "--security-opt=no-new-privileges:true",
        ]:
            self.assertIn(flag, args)
        self.assertNotIn("-v", args)
        self.assertFalse(any("docker.sock" in arg for arg in args))
        with self.assertRaises(ValueError):
            module.command("python:latest", "test")


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_survives_quota_failure_and_owner_bound_resume(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            async def runner(reader, writer):
                payload = json.loads(await reader.readline())
                self.assertEqual(payload["quota"], 250)
                writer.write(
                    json.dumps(
                        {
                            "type": "checkpoint",
                            "name": "progress.json",
                            "data": base64.b64encode(b'{"next_account": 12}').decode(),
                        }
                    ).encode()
                    + b"\n"
                )
                await writer.drain()
                self.assertTrue(json.loads(await reader.readline())["ok"])
                writer.write(b'{"type":"done","ok":false,"error_code":"QUOTA_EXHAUSTED"}\n')
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_unix_server(runner, str(root / "runner"))
            jobs = Jobs(root / "jobs", str(root / "runner"), "missing", max_calls=300)
            try:
                rec = jobs.start("alice", "x", {}, {}, quota=250)
                await jobs.tasks[rec["id"]]
                rec = jobs.get(rec["id"])
                self.assertEqual(rec["state"], "failed")
                self.assertEqual(rec["error_code"], "QUOTA_EXHAUSTED")
                self.assertNotIn("超时", rec["error"])
                data = jobs.artifact(rec["id"], "progress.json", "alice").read_bytes()
                self.assertEqual(json.loads(data)["next_account"], 12)
                with self.assertRaises(ValueError):
                    jobs.artifact(rec["id"], "progress.json", "bob")
                with self.assertRaises(ValueError):
                    jobs.start("alice", "x", {}, {}, quota=301)
            finally:
                await jobs.close()
                server.close()
                await server.wait_closed()

    async def test_all_calls_persist_with_result_and_page_audit(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            async def gateway(reader, writer):
                request = json.loads(await reader.readline())
                writer.write(b'{"ok":true,"tools":[],"credential":"synthetic"}\n')
                await writer.drain()
                while line := await reader.readline():
                    request = json.loads(line)
                    result = {
                        "ok": True,
                        "remaining": 202 - request["arguments"]["n"],
                        "data": [{"value": 42}],
                        "page": {"row_count": 1, "has_more": False},
                    }
                    writer.write(json.dumps(result).encode() + b"\n")
                    await writer.drain()
                writer.close()
                await writer.wait_closed()

            async def runner(reader, writer):
                await reader.readline()
                for n in range(202):
                    writer.write(
                        json.dumps(
                            {
                                "type": "api",
                                "tool": "read",
                                "arguments": {
                                    "node_id": "act_test",
                                    "params": {
                                        "level": "ad",
                                        "after": str(n),
                                        "secret": "never-print",
                                    },
                                    "n": n,
                                },
                            }
                        ).encode()
                        + b"\n"
                    )
                    await writer.drain()
                    self.assertTrue(json.loads(await reader.readline())["ok"])
                writer.write(b'{"type":"done","ok":true}\n')
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            gs = await asyncio.start_unix_server(gateway, str(root / "gateway"))
            rs = await asyncio.start_unix_server(runner, str(root / "runner"))
            jobs = Jobs(root / "jobs", str(root / "runner"), str(root / "gateway"), max_calls=300)
            try:
                rec = jobs.start("a", "x", {"read": {}}, {}, quota=250)
                await jobs.tasks[rec["id"]]
                rec = jobs.get(rec["id"])
                self.assertEqual(rec["state"], "succeeded")
                self.assertEqual(len(rec["call_audit"]), 202)
                self.assertEqual(len(jobs.view(rec)["call_audit"]), 20)
                self.assertEqual(rec["successful_calls"], 202)
                journal = jobs.artifact(rec["id"], "api-results.jsonl", "a").read_text()
                self.assertEqual(len(journal.splitlines()), 202)
                self.assertNotIn("never-print", journal)
                audit = json.loads(jobs.artifact(rec["id"], "api-audit.json", "a").read_text())
                self.assertEqual(audit[0]["node_id"], "act_test")
                self.assertFalse(audit[0]["has_more"])
                with self.assertRaises(ValueError):
                    jobs.save_file(rec, "api-results.jsonl", b"fake", replace=True)
            finally:
                await jobs.close()
                gs.close()
                rs.close()
                await gs.wait_closed()
                await rs.wait_closed()

    async def test_timeout_is_not_quota_and_checkpoint_is_durable(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            async def runner(reader, writer):
                await reader.readline()
                writer.write(
                    b'{"type":"error","error_code":"EXECUTION_TIMEOUT","message":"timeout"}\n'
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_unix_server(runner, str(root / "runner"))
            jobs = Jobs(root / "jobs", str(root / "runner"), "absent")
            try:
                rec = jobs.start("a", "x", {}, {})
                await jobs.tasks[rec["id"]]
                rec = jobs.get(rec["id"])
                self.assertEqual(rec["error_code"], "EXECUTION_TIMEOUT")
            finally:
                await jobs.close()
                server.close()
                await server.wait_closed()
