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
        assert len(plugin.tools) == 5
        assert all(t.handler_module_path == package.__name__ + ".main" for t in plugin.tools)
        event = types.SimpleNamespace(
            unified_msg_origin="test:group:1",
            get_sender_id=lambda: "alice",
            get_messages=lambda: [],
        )
        wrapped = types.SimpleNamespace(context=types.SimpleNamespace(event=event))
        guide = next(t for t in plugin.tools if t.name == "code_guide")
        for section in module.SECTIONS:
            result = json.loads((await guide.call(wrapped, section=section)).content[0].text)
            assert result["ok"] and result["content"] and len(result["sha256"]) == 64
        assert not plugin.jobs.records
        # Real request builder includes persona routing and the registered guide tool.
        from astrbot.core.astr_main_agent import _ensure_persona_and_skills

        routing = '批量任务先调用 code_guide(section="overview")，按预算保存检查点并续接。'

        async def resolve(**kwargs):
            return "test", {"prompt": routing, "tools": None, "skills": []}, None, False

        context.persona_manager = types.SimpleNamespace(resolve_selected_persona=resolve)
        context.subagent_orchestrator = None
        event.get_extra = lambda key, default=None: default
        event.get_platform_name = lambda: "test"
        req = types.SimpleNamespace(
            system_prompt="",
            conversation=types.SimpleNamespace(persona_id="test"),
            contexts=[],
            func_tool=None,
        )
        await _ensure_persona_and_skills(req, {"computer_use_runtime": "none"}, context, event)
        assert routing in req.system_prompt
        assert any(t.name == "code_guide" for t in req.func_tool)
        assert (await guide.call(wrapped, section="../../cmd_config.json")).isError
        plugin.config["enabled"] = False
        assert (await guide.call(wrapped)).isError
        plugin.config["enabled"] = True
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
