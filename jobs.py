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
        raise JobError(
            result.get("error_code", "GATEWAY_REJECTED"), result.get("error", "网关拒绝请求")
        )
    return result


class JobError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def audit_call(tool, arguments, result, elapsed):
    arguments = arguments if isinstance(arguments, dict) else {}
    params = arguments.get("params", {})
    params = params if isinstance(params, dict) else {}
    node = arguments.get("node_id", "")
    node = node if isinstance(node, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", node) else None
    cursor = params.get("after")
    page = result.get("page") or result.get("remote_page") or {}
    stable = {k: v for k, v in arguments.items() if k != "params"}
    stable["params"] = {k: v for k, v in params.items() if k != "after"}
    return {
        "tool": str(tool)[:64],
        "node_id": node,
        "query_sha256": hashlib.sha256(
            json.dumps(stable, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest(),
        "cursor_sha256": hashlib.sha256(str(cursor).encode()).hexdigest() if cursor else None,
        "level": params.get("level")
        if params.get("level") in {"account", "campaign", "adset", "ad"}
        else None,
        "ok": result.get("ok") is True,
        "status": result.get("status"),
        "error_code": result.get("error_code") or ("API_FAILED" if not result.get("ok") else None),
        "row_count": page.get("row_count"),
        "has_more": page.get("has_more"),
        "time": time.time(),
        "elapsed_ms": elapsed,
    }


class Jobs:
    def __init__(self, root, runner_socket, gateway_socket, max_calls=200):
        if type(max_calls) is not int or not 1 <= max_calls <= 10000:
            raise ValueError("管理员调用额度须为 1–10000")
        self.max_calls = max_calls
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
        result = {k: v for k, v in record.items() if k not in {"owner", "call_audit"}}
        result["call_audit"] = record.get("call_audit", [])[-20:]
        result["audit_count"] = len(record.get("call_audit", []))
        result["seconds_left"] = (
            max(0, round(record["created"] + record["timeout"] - time.time()))
            if record["state"] == "running"
            else 0
        )
        result["data_completeness"] = (
            "未自动认证业务范围完整；请依据完整审计与检查点核对账户、分页和失败项"
        )
        result["resume_hint"] = (
            "新任务通过 previous_files 读取已保存结果/检查点，重新声明 api_scopes；不自动重放写请求。"
        )
        return result

    def start(self, owner, code, scopes, inputs, timeout=120, quota=50):
        self.prune()
        if self.closed or any(not t.done() for t in self.tasks.values()):
            raise ValueError("执行器已关闭或有任务运行中，请稍后重试")
        if not isinstance(code, str) or not 1 <= len(code.encode()) <= 128000:
            raise ValueError("代码长度须为 1–128000 字节")
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("运行时间须为 1–600 秒")
        if type(quota) is not int or not 1 <= quota <= self.max_calls:
            raise ValueError(f"调用额度须为 1–{self.max_calls} 次（管理员上限）")
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
            "quota": quota,
            "remaining": quota,
            "successful_calls": 0,
            "failed_calls": 0,
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
                        "quota": quota,
                    },
                )
                finished = False
                while line := await reader.readline():
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "api":
                        if record["calls"] >= quota:
                            await send(
                                writer,
                                {
                                    "ok": False,
                                    "error_code": "QUOTA_EXHAUSTED",
                                    "error": "调用额度耗尽",
                                    "remaining": 0,
                                },
                            )
                            continue
                        started = time.monotonic()
                        record["calls"] += 1
                        record["remaining"] = quota - record["calls"]
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
                        audit = audit_call(
                            event.get("tool"),
                            event.get("arguments"),
                            result,
                            round((time.monotonic() - started) * 1000),
                        )
                        record["call_audit"].append(audit)
                        record["successful_calls" if audit["ok"] else "failed_calls"] += 1
                        entry = (
                            json.dumps(
                                {"sequence": record["calls"], "audit": audit, "response": result},
                                ensure_ascii=True,
                            ).encode()
                            + b"\n"
                        )
                        try:
                            self.append_result(record, entry)
                        except ValueError:
                            result = {
                                "ok": False,
                                "error_code": "RESULT_STORAGE_LIMIT",
                                "error": "结果存储额度耗尽；本次远端请求可能已成功，勿自动重放写请求",
                            }
                        self.persist(record)
                        await send(writer, result)
                    elif kind == "checkpoint":
                        try:
                            name = filename(event["name"])
                            data = base64.b64decode(event["data"], validate=True)
                            json.loads(data)
                            self.save_file(record, name, data, replace=True)
                            self.persist(record)
                            await send(writer, {"ok": True})
                        except (ValueError, KeyError, TypeError):
                            await send(
                                writer,
                                {
                                    "ok": False,
                                    "error_code": "CHECKPOINT_FAILED",
                                    "error": "检查点名称、JSON 或存储额度无效",
                                },
                            )
                    elif kind == "output":
                        record["output"] = (record["output"] + str(event.get("text", "")))[:64000]
                    elif kind == "file":
                        self.save_file(
                            record,
                            filename(event["name"]),
                            base64.b64decode(event["data"], validate=True),
                        )
                    elif kind == "done":
                        record["state"] = "succeeded" if event.get("ok") is True else "failed"
                        record["error_code"] = (
                            event.get("error_code") if not event.get("ok") else None
                        )
                        if record["error_code"]:
                            record["error"] = {
                                "QUOTA_EXHAUSTED": "调用额度耗尽；已保存结果可用于下一段任务",
                                "PYTHON_ERROR": "Python 代码执行失败，请检查输出",
                            }.get(record["error_code"], "执行未完成，请查看输出及审计")
                        finished = True
                        break
                    elif kind == "error":
                        raise JobError(
                            event.get("error_code", "RUNNER_FAILED"),
                            event.get("message", "执行服务失败"),
                        )
                    else:
                        raise JobError("PROTOCOL_ERROR", "执行协议无效")
                    self.persist(record)
                if not finished:
                    raise ValueError("执行服务连接已中断")
        except asyncio.CancelledError:
            record["state"] = "stopped"
            record["error_code"] = "STOPPED"
        except Exception as exc:
            record["state"] = "failed"
            record["error_code"] = getattr(
                exc,
                "code",
                "EXECUTION_TIMEOUT" if isinstance(exc, TimeoutError) else "TRANSPORT_ERROR",
            )
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
            try:
                self.save_file(
                    record,
                    "api-audit.json",
                    json.dumps(record["call_audit"], ensure_ascii=True).encode(),
                    internal=True,
                )
            except ValueError:
                record["audit_file_error"] = "审计文件超限，原始记录仍在任务记录中"
            self.persist(record)

    def save_file(self, record, name, data, *, replace=False, internal=False):
        filename(name)
        if not internal and name in {"api-results.jsonl", "api-audit.json"}:
            raise ValueError("文件名由系统保留")
        old = next((f for f in record["files"] if f["name"] == name), None)
        if old and not replace:
            raise ValueError("文件已存在；检查点请使用 checkpoint 更新")
        if (not old and len(record["files"]) >= (22 if internal else 20)) or sum(
            f["bytes"] for f in record["files"] if f["name"] != name
        ) + len(data) > (10 if internal else 8) * 1024 * 1024:
            raise ValueError("结果文件超过存储限额")
        directory = self.root / record["id"] / "files"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / name
        temp = directory / (".save-" + uuid.uuid4().hex)
        temp.write_bytes(data)
        temp.chmod(0o600)
        temp.replace(path)
        if old:
            record["files"].remove(old)
        record["files"].append(
            {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )

    def append_result(self, record, entry):
        name = "api-results.jsonl"
        path = self.root / record["id"] / "files" / name
        previous = path.read_bytes() if path.exists() else b""
        if len(previous) + len(entry) > 7 * 1024 * 1024:
            raise ValueError("API 结果日志超过 7 MiB，请分段执行")
        self.save_file(record, name, previous + entry, replace=True, internal=True)

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
