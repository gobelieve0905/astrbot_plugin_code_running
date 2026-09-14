"""Run as an administrator on Linux; synthetic data, no platform API or messages."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))
from service import Runner, timeout  # noqa: E402

CODE = """
import os, socket
from pathlib import Path
assert os.getuid() == 65532
for name in ['/astrbot', '/srv/apps/astrbot', '/var/run/docker.sock', '/run/docker.sock']:
    assert not Path(name).exists(), name
try:
    Path('/etc/escape').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('root filesystem writable')
s = socket.socket()
s.settimeout(.3)
assert s.connect_ex(('1.1.1.1', 443)) != 0
s.close()
assert not any(k for k in os.environ if 'TOKEN' in k or 'PROXY' in k)
from controlled_api import call
result = call('synthetic_read', node_id='act_test')
assert result['data'] == [1, 2, 3]
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
frame = pd.DataFrame({'value': result['data']})
frame.to_csv('output/result.csv', index=False)
frame.to_excel('output/result.xlsx', index=False)
frame.plot.bar()
plt.savefig('output/chart.png')
print('isolated analysis passed', int(frame.value.sum()))
"""


async def main():
    with tempfile.TemporaryDirectory() as root:
        path = str(Path(root) / "runner.sock")
        runner = Runner(sys.argv[1])
        server = await asyncio.start_unix_server(runner.handle, path, limit=12 * 1024 * 1024)

        async def start(code, seconds=60):
            reader, writer = await asyncio.open_unix_connection(path, limit=12 * 1024 * 1024)
            writer.write(
                json.dumps({"code": code, "inputs": {}, "tools": [], "timeout": seconds}).encode()
                + b"\n"
            )
            await writer.drain()
            return reader, writer

        async def idle():
            async with timeout(15):
                while runner.busy:
                    await asyncio.sleep(0.1)

        try:
            reader, writer = await start(CODE)
            events = []
            async with timeout(90):
                while line := await reader.readline():
                    event = json.loads(line)
                    events.append(event)
                    if event["type"] == "api":
                        assert event["tool"] == "synthetic_read"
                        writer.write(b'{"ok":true,"data":[1,2,3]}\n')
                        await writer.drain()
                    if event["type"] in ("done", "error"):
                        break
            assert events[-1] == {"type": "done", "ok": True}, events[-1]
            assert {e["name"] for e in events if e["type"] == "file"} == {
                "result.csv",
                "result.xlsx",
                "chart.png",
            }
            writer.close()
            await writer.wait_closed()
            await idle()
            # A forked child must die with the entire task container on disconnect.
            reader, writer = await start(
                "import subprocess,time; subprocess.Popen(['sleep','300']); print('ready'); time.sleep(300)",
                300,
            )
            async with timeout(30):
                while True:
                    event = json.loads(await reader.readline())
                    if event.get("type") == "output":
                        break
            writer.close()
            await writer.wait_closed()
            await idle()
            reader, writer = await start("while True: pass", 1)
            async with timeout(20):
                result = json.loads(await reader.readline())
                assert result["type"] == "error", result
            writer.close()
            await writer.wait_closed()
            await idle()
            # Malformed protocol is fatal, not shell commands or a host path operation.
            reader, writer = await start("import os; os.write(1,b'not-json\\n')")
            async with timeout(20):
                assert json.loads(await reader.readline())["type"] == "error"
            writer.close()
            await writer.wait_closed()
            await idle()
            print(
                "Sandbox smoke passed: network denial, no host mounts, readonly, nonroot, API relay, CSV/XLSX/PNG, stop child, timeout, invalid frame"
            )
        finally:
            server.close()
            await server.wait_closed()


asyncio.run(main())
