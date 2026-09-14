"""Bounded, session-owned jobs; only JSON crosses the sandbox boundary."""

import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

MAX_FRAME = 12 * 1024 * 1024
NAME = re.compile(r"[^/\\\x00-\x1f]{1,120}\Z")


def filename(name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError("文件名无效")
    return name


async def send(writer, value):
    writer.write(json.dumps(value, ensure_ascii=True).encode() + b"\n")
    await writer.drain()


async def rpc(reader, writer, payload):
    await send(writer, payload)
    result = json.loads(await reader.readline())
    if not result.get("ok"):
        raise ValueError(result.get("error", "网关拒绝请求"))
    return result


class Jobs:
    def __init__(self, root, runner_socket, gateway_socket):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner_socket, self.gateway_socket = runner_socket, gateway_socket
        self.records, self.tasks = {}, {}
        self.closed = False
        for path in self.root.glob("*/record.json"):
            try:
                record = json.loads(path.read_text())
                if record["id"] != path.parent.name:
                    continue
                if record["state"] == "running":
                    record["state"] = "interrupted"
                self.records[record["id"]] = record
                self.persist(record)
            except (ValueError, OSError, KeyError):
                continue
        self.prune()

    def persist(self, record):
        path = self.root / record["id"] / "record.json"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False))
        temp.chmod(0o600)
        temp.replace(path)

    def prune(self):
        import shutil

        for record in sorted(self.records.values(), key=lambda r: r["created"]):
            if record["state"] != "running" and (
                time.time() - record["created"] > 86400 or len(self.records) >= 50
            ):
                shutil.rmtree(self.root / record["id"])
                del self.records[record["id"]]

    def get(self, job_id, owner=None):
        self.prune()
        record = self.records.get(job_id)
        if record is None or (owner is not None and record["owner"] != owner):
            raise ValueError("任务不存在、已过期或不属于当前会话用户")
        return record

    def view(self, record):
        return {k: v for k, v in record.items() if k != "owner"}

    def start(self, owner, code, scopes, inputs, timeout=120, quota=50):
        self.prune()
        if self.closed or any(not t.done() for t in self.tasks.values()):
            raise ValueError("执行器已关闭或有任务运行中，请稍后重试")
        if not isinstance(code, str) or not 1 <= len(code.encode()) <= 128000:
            raise ValueError("代码长度须为 1–128000 字节")
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("运行时间须为 1–600 秒")
        if type(quota) is not int or not 1 <= quota <= 200:
            raise ValueError("调用额度须为 1–200 次")
        if not isinstance(scopes, dict):
            raise ValueError("api_scopes 须为对象")
        if len(inputs) > 20 or sum(len(v) for v in inputs.values()) > 8 * 1024 * 1024:
            raise ValueError("输入最多 20 个文件、总计 8 MiB")
        for name in inputs:
            filename(name)
        record = {
            "id": uuid.uuid4().hex,
            "owner": owner,
            "created": time.time(),
            "state": "running",
            "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "operations": list(scopes),
            "calls": 0,
            "call_audit": [],
            "output": "",
            "files": [],
            "timeout": timeout,
        }
        self.records[record["id"]] = record
        self.persist(record)
        task = asyncio.create_task(self.run(record, code, scopes, inputs, timeout, quota))
        self.tasks[record["id"]] = task
        task.add_done_callback(lambda _: self.tasks.pop(record["id"], None))
        return self.view(record)

    async def run(self, record, code, scopes, inputs, timeout, quota):
        writers = []
        try:
            async with asyncio.timeout(timeout + 15):
                tools, credential = [], None
                if scopes:
                    gr, gw = await asyncio.open_unix_connection(
                        self.gateway_socket, limit=MAX_FRAME
                    )
                    writers.append(gw)
                    grant = await rpc(
                        gr,
                        gw,
                        {"action": "issue", "scopes": scopes, "ttl": timeout, "quota": quota},
                    )
                    tools, credential = grant["tools"], grant["credential"]
                reader, writer = await asyncio.open_unix_connection(
                    self.runner_socket, limit=MAX_FRAME
                )
                writers.append(writer)
                await send(
                    writer,
                    {
                        "code": code,
                        "inputs": {k: base64.b64encode(v).decode() for k, v in inputs.items()},
                        "tools": tools,
                        "timeout": timeout,
                    },
                )
                size, finished, seen = 0, False, set()
                while line := await reader.readline():
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "api":
                        record["calls"] += 1
                        if not credential:
                            result = {"ok": False, "error": "本任务未授权 API 操作"}
                        else:
                            await send(
                                gw,
                                {
                                    "action": "call",
                                    "credential": credential,
                                    "tool": event.get("tool"),
                                    "arguments": event.get("arguments"),
                                },
                            )
                            result = json.loads(await gr.readline())
                        record["call_audit"].append(
                            {
                                "tool": str(event.get("tool", ""))[:64],
                                "ok": result.get("ok") is True,
                                "time": time.time(),
                            }
                        )
                        record["call_audit"] = record["call_audit"][-200:]
                        await send(writer, result)
                    elif kind == "output":
                        record["output"] = (record["output"] + str(event.get("text", "")))[:64000]
                    elif kind == "file":
                        name = filename(event["name"])
                        if name in seen or len(seen) >= 20:
                            raise ValueError("文件重复或超过 20 个")
                        data = base64.b64decode(event["data"], validate=True)
                        size += len(data)
                        if size > 8 * 1024 * 1024:
                            raise ValueError("输出文件超过 8 MiB")
                        directory = self.root / record["id"] / "files"
                        directory.mkdir(mode=0o700, exist_ok=True)
                        path = directory / name
                        with path.open("xb") as output:
                            output.write(data)
                        path.chmod(0o600)
                        seen.add(name)
                        record["files"].append(
                            {
                                "name": name,
                                "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                        )
                    elif kind == "done":
                        record["state"] = "succeeded" if event.get("ok") is True else "failed"
                        finished = True
                        break
                    else:
                        raise ValueError("执行服务失败或超时")
                    self.persist(record)
                if not finished:
                    raise ValueError("执行服务连接已中断")
        except asyncio.CancelledError:
            record["state"] = "stopped"
        except Exception as exc:
            record["state"] = "failed"
            # Do not expose arbitrary transport exceptions, paths, or credentials.
            record["error"] = (
                str(exc)[:300]
                if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
                else type(exc).__name__
            )
        finally:
            for writer in reversed(writers):
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
            record["finished"] = time.time()
            self.persist(record)

    async def stop(self, job_id, owner=None):
        record = self.get(job_id, owner)
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if record["state"] == "running":
                record["state"] = "stopped"
                record["finished"] = time.time()
                self.persist(record)
        return self.view(record)

    def artifact(self, job_id, name, owner):
        record = self.get(job_id, owner)
        filename(name)
        entry = next((f for f in record["files"] if f["name"] == name), None)
        if entry is None:
            raise ValueError("结果文件不存在")
        path = self.root / job_id / "files" / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise ValueError("结果文件已变化")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("结果文件校验失败")
        return path

    async def close(self):
        self.closed = True
        for job_id in tuple(self.tasks):
            await self.stop(job_id)
