"""Untrusted-container entry point. Isolation is enforced by Docker, never by Python."""

import base64
import io
import json
import os
import sys
import time
import traceback
import types
from pathlib import Path

wire = sys.stdout
source = sys.stdin


def emit(value):
    wire.write(json.dumps(value, ensure_ascii=True) + "\n")
    wire.flush()


class Output(io.TextIOBase):
    used = 0

    def write(self, text):
        text = str(text)
        room = max(0, 64000 - self.used)
        if room:
            emit({"type": "output", "text": text[:room]})
            self.used += len(text[:room])
        return len(text)

    def flush(self):
        pass


remaining = 0
deadline = 0


def budget():
    return {"remaining": remaining, "seconds_left": max(0, int(deadline - time.monotonic()))}


class APIError(RuntimeError):
    def __init__(self, reply):
        self.code = reply.get("error_code", "API_FAILED")
        self.response = reply
        super().__init__(json.dumps(reply, ensure_ascii=False))


def checkpoint(name, value):
    """Persist JSON progress immediately; acknowledged before returning."""
    emit(
        {
            "type": "checkpoint",
            "name": name,
            "data": base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode(),
        }
    )
    reply = json.loads(source.readline())
    if not reply.get("ok"):
        raise APIError(reply)


def call(tool, **arguments):
    """One gateway request; pagination/retries are explicit and each consumes quota."""
    global remaining
    emit({"type": "api", "tool": tool, "arguments": arguments})
    reply = json.loads(source.readline())
    remaining = reply.get("remaining", max(0, remaining - 1))
    if not reply.get("ok"):
        raise APIError(reply)
    return reply


def main():
    global remaining, deadline
    payload = json.loads(source.readline())
    remaining = payload.get("quota", 200)
    deadline = time.monotonic() + payload.get("timeout", 120)
    os.chdir("/work")
    Path("input").mkdir()
    Path("output").mkdir()
    for name, data in payload.get("inputs", {}).items():
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid input name")
        Path("input", name).write_bytes(base64.b64decode(data, validate=True))
    sdk = types.ModuleType("controlled_api")
    sdk.call = call
    sdk.APIError = APIError
    sdk.checkpoint = checkpoint
    sdk.budget = budget
    sdk.tools = payload.get("tools", [])
    sys.modules["controlled_api"] = sdk
    sys.stdout = sys.stderr = Output()
    ok = True
    error_code = None
    try:
        exec(compile(payload["code"], "<task>", "exec"), {"__name__": "__main__"})
    except BaseException as exc:
        error_code = exc.code if isinstance(exc, APIError) else "PYTHON_ERROR"
        ok = False
        traceback.print_exc(limit=12)
    total = 0
    for path in sorted(Path("output").iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if total > 8 * 1024 * 1024:
            raise ValueError("Output files exceed 8 MiB")
        emit(
            {
                "type": "file",
                "name": path.name,
                "data": base64.b64encode(path.read_bytes()).decode(),
            }
        )
    emit({"type": "done", "ok": ok, "error_code": error_code})


if __name__ == "__main__":
    main()
