"""Untrusted-container entry point. Isolation is enforced by Docker, never by Python."""

import base64
import io
import json
import os
import sys
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


def call(tool, **arguments):
    """One gateway request; pagination/retries are explicit and each consumes quota."""
    emit({"type": "api", "tool": tool, "arguments": arguments})
    reply = json.loads(source.readline())
    if not reply.get("ok"):
        raise RuntimeError(json.dumps(reply, ensure_ascii=False))
    return reply


def main():
    payload = json.loads(source.readline())
    os.chdir("/work")
    Path("input").mkdir()
    Path("output").mkdir()
    for name, data in payload.get("inputs", {}).items():
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid input name")
        Path("input", name).write_bytes(base64.b64decode(data, validate=True))
    sdk = types.ModuleType("controlled_api")
    sdk.call = call
    sdk.tools = payload.get("tools", [])
    sys.modules["controlled_api"] = sdk
    sys.stdout = sys.stderr = Output()
    ok = True
    try:
        exec(compile(payload["code"], "<task>", "exec"), {"__name__": "__main__"})
    except BaseException:
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
    emit({"type": "done", "ok": ok})


if __name__ == "__main__":
    main()
