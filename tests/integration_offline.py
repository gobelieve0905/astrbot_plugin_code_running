"""Real AstrBot registration and session isolation, no adapter startup or network."""

import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path


async def main():
    with tempfile.TemporaryDirectory() as root:
        os.environ["ASTRBOT_ROOT"] = root
        from astrbot.core.agent.tool import FunctionTool
        from astrbot.core.provider.func_tool_manager import FuncCall
        from astrbot.core.star.context import Context

        package = types.ModuleType("data.plugins.astrbot_plugin_code_running")
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        sys.modules[package.__name__] = package
        module = importlib.import_module(package.__name__ + ".main")
        context = Context.__new__(Context)
        context._config = {}
        context.provider_manager = types.SimpleNamespace(llm_tools=FuncCall())
        foreign = FunctionTool(
            name="foreign", description="unrelated", parameters={"type": "object"}
        )
        context.get_llm_tool_manager().func_list.append(foreign)
        plugin = module.CodeRunningPlugin(context, {"enabled": True})
        await plugin.initialize()
        assert len(plugin.tools) == 4
        assert all(t.handler_module_path == package.__name__ + ".main" for t in plugin.tools)
        event = types.SimpleNamespace(
            unified_msg_origin="test:group:1",
            get_sender_id=lambda: "alice",
            get_messages=lambda: [],
        )
        wrapped = types.SimpleNamespace(context=types.SimpleNamespace(event=event))
        result = await plugin.tools[0].call(
            wrapped, code="print(42)", input_texts={"data.csv": "x\n1"}
        )
        started = json.loads(result.content[0].text)
        assert started["ok"]
        job_id = started["id"]
        await plugin.jobs.tasks[job_id]
        status = json.loads((await plugin.tools[1].call(wrapped, job_id=job_id)).content[0].text)
        assert (
            status["ok"] and status["state"] == "failed"
        )  # Runner absent: no host execution fallback.
        event.get_sender_id = lambda: "bob"
        assert (await plugin.tools[1].call(wrapped, job_id=job_id)).isError
        assert (
            await plugin.tools[0].call(wrapped, code="print(42)", input_texts={"../secret": "x"})
        ).isError
        cached = plugin.tools[0]
        await plugin.terminate()
        assert context.get_llm_tool_manager().func_list == [foreign]
        assert not context.registered_web_apis
        assert (await cached.call(wrapped, code="print(42)")).isError
        print("Real AstrBot integration passed: tools, ownership, fail-closed execution, unload")


asyncio.run(main())
