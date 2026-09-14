"""Host-side fixed-policy runner. Only this administrator-installed service uses Docker."""

import asyncio
import json
import os
import re
import signal
import socket
import struct
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

MAX_FRAME = 12 * 1024 * 1024


@asynccontextmanager
async def timeout(seconds):
    task = asyncio.current_task()
    expired = False

    def cancel():
        nonlocal expired
        expired = True
        task.cancel()

    handle = asyncio.get_running_loop().call_later(seconds, cancel)
    try:
        yield
    except asyncio.CancelledError:
        if expired:
            raise TimeoutError("Runner timeout") from None
        raise
    finally:
        handle.cancel()


def command(image, name):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("Runner requires an immutable local image ID")
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--label",
        "astrbot.code-running=true",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--memory=256m",
        "--memory-swap=256m",
        "--cpus=0.5",
        "--pids-limit=32",
        "--cgroup-parent=apps.slice",
        "--ulimit=nofile=128:128",
        "--ulimit=fsize=16777216:16777216",
        "--log-driver=none",
        "--ipc=none",
        "--tmpfs=/work:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--workdir=/work",
        "--env=HOME=/work",
        "--env=MPLCONFIGDIR=/tmp/mpl",
        "--env=OPENBLAS_NUM_THREADS=1",
        "--env=OMP_NUM_THREADS=1",
        "--env=MKL_NUM_THREADS=1",
        "--env=NUMEXPR_NUM_THREADS=1",
        "--entrypoint=python3",
        image,
        "-I",
        "-B",
        "-u",
        "-c",
        Path(__file__).with_name("bootstrap.py").read_text(),
    ]


async def send(writer, value):
    writer.write(json.dumps(value, ensure_ascii=True).encode() + b"\n")
    await writer.drain()


class Runner:
    def __init__(self, image, uid=997):
        self.image, self.uid = image, uid
        self.busy = False  # One global slot: shared-host resource budget.

    async def handle(self, reader, writer):
        process = None
        name = "astrbot-code-" + uuid.uuid4().hex
        acquired = False
        background = []
        try:
            sock = writer.get_extra_info("socket")
            _, uid, _ = struct.unpack(
                "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid not in (0, self.uid):
                raise ValueError("Unauthorized runner peer")
            async with timeout(620):
                payload = json.loads(await asyncio.wait_for(reader.readline(), 10))
                if payload.get("action") == "health":
                    await send(writer, {"ok": True, "busy": self.busy, "protocol": 1})
                    return
                if self.busy:
                    raise ValueError("Runner busy; retry later")
                if set(payload) - {"code", "inputs", "tools", "timeout"}:
                    raise ValueError("Unknown runner option")
                if (
                    not isinstance(payload.get("code"), str)
                    or len(payload["code"].encode()) > 128000
                ):
                    raise ValueError("Invalid code")
                seconds = payload.get("timeout", 120)
                if type(seconds) is not int or not 1 <= seconds <= 600:
                    raise ValueError("Invalid timeout")
                self.busy = acquired = True
                process = await asyncio.create_subprocess_exec(
                    *command(self.image, name),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=MAX_FRAME,
                )
                replies = asyncio.Queue(maxsize=1)

                async def receive():
                    while True:
                        line = await reader.readline()
                        if not line:
                            raise ConnectionError("Client disconnected")
                        await replies.put(json.loads(line))

                async def exchange():
                    await send(process.stdin, payload)
                    size, calls = 0, 0
                    finished = False
                    while line := await process.stdout.readline():
                        size += len(line)
                        if size > 13 * 1024 * 1024:
                            raise ValueError("Output limit exceeded")
                        event = json.loads(line)
                        kind = event.get("type")
                        if kind not in {"api", "output", "file", "done"} or finished:
                            raise ValueError("Invalid sandbox frame")
                        if kind == "done":
                            finished = True
                            # Finish is acknowledged only after a clean process exit.
                            completion = event
                            continue
                        await send(writer, event)
                        if kind == "api":
                            calls += 1
                            if calls > 200:
                                raise ValueError("API frame quota exceeded")
                            await send(process.stdin, await replies.get())
                    code = await process.wait()
                    if code != 0 or not finished:
                        raise ValueError("Sandbox exited without successful protocol completion")
                    await send(writer, completion)

                async def drain_stderr():
                    size = 0
                    while chunk := await process.stderr.read(4096):
                        size += len(chunk)
                        if size > 64000:
                            raise ValueError("Sandbox stderr limit exceeded")

                background = [
                    asyncio.create_task(receive()),
                    asyncio.create_task(exchange()),
                    asyncio.create_task(drain_stderr()),
                ]
                async with timeout(seconds):
                    while not background[1].done():
                        watched = [t for t in background if not t.done()]
                        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
        except (Exception, asyncio.CancelledError) as exc:
            try:
                await send(
                    writer,
                    {
                        "type": "error",
                        "error": type(exc).__name__,
                        "message": "执行失败、超时或已停止；资源已回收",
                    },
                )
            except (ConnectionError, RuntimeError):
                pass
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            if acquired:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "-f",
                    name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()
                if process:
                    await process.wait()
                self.busy = False
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass


async def main():
    image = os.environ["CODE_RUNNING_IMAGE"]
    command(image, "validate")
    path = Path(os.environ["CODE_RUNNING_SOCKET"])
    if path.exists():
        path.unlink()
    service = Runner(image)
    server = await asyncio.start_unix_server(service.handle, path=str(path), limit=MAX_FRAME)
    os.chown(path, 997, 997)
    path.chmod(0o600)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
