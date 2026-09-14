"""AstrBot adapter for independent, isolated Python jobs."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request
from astrbot.core.agent.tool import FunctionTool
from mcp.types import CallToolResult, TextContent

from .jobs import Jobs, filename

PARAMETERS = {
    "code_start": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "完整 Python。from controlled_api import call, tools, APIError, checkpoint, budget。call 返回完整单页响应，失败抛 APIError（.code/.response）；不要用 if not ok 捕获异常。budget() 返回剩余额度/秒数。checkpoint(文件名, JSON对象) 立即保存并确认。每次 API 响应自动记录在 api-results.jsonl；接近预算时保存游标并结束，新任务用 previous_files 续接，不重放写操作。input/ 是输入，output/ 是最终文件。",
            },
            "api_scopes": {
                "type": "object",
                "description": "工具名→固定参数约束，例如 {Meta_get_insights: {node_id: act_123}}；只选本任务需要的已启用 API 工具。空对象表示纯计算。",
                "additionalProperties": {"type": "object"},
            },
            "attachment_indexes": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "maxItems": 20,
                "description": "当前消息文件附件的从 0 开始的序号；不接受宿主路径或 URL",
            },
            "input_texts": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "文件名到文本内容，可传 CSV/JSON",
            },
            "previous_files": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}, "name": {"type": "string"}},
                    "required": ["job_id", "name"],
                    "additionalProperties": False,
                },
            },
            "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
            "quota": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 50},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
    "code_status": {
        "type": "object",
        "properties": {"job_id": {"type": "string"}},
        "required": ["job_id"],
        "additionalProperties": False,
    },
    "code_stop": {
        "type": "object",
        "properties": {"job_id": {"type": "string"}},
        "required": ["job_id"],
        "additionalProperties": False,
    },
    "code_file": {
        "type": "object",
        "properties": {"job_id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["job_id", "name"],
        "additionalProperties": False,
    },
}
PARAMETERS["code_status"]["properties"]["wait_seconds"] = {
    "type": "integer",
    "minimum": 0,
    "maximum": 20,
    "default": 10,
    "description": "任务仍在运行时最多等待的秒数，避免频繁轮询",
}

DESCRIPTIONS = {
    "code_start": "启动隔离 Python 长任务，立即返回任务 ID。无外网/宿主文件访问；API 只能通过 controlled_api.call 使用 api_scopes 内操作，每次分页/重试重新检查后台权限。不要自动重试写操作。可独立处理文件与计算，失败后修正代码创建新任务；用 code_status 获取进度，完成后用 code_file 发送结果文件。",
    "code_status": "查询当前会话用户的 Python 任务状态、输出、错误及结果文件；仍在运行时稍后再查询。",
    "code_stop": "停止当前会话用户的 Python 任务并撤销任务通道，远端已发出的写请求可能已经执行。",
    "code_file": "把当前会话用户任务生成的指定文件发送到当前会话。只接受 code_status 列出的文件名。",
}


def identity(context):
    event = context.context.event
    value = str(event.unified_msg_origin) + "\0" + str(event.get_sender_id())
    return event, hashlib.sha256(value.encode()).hexdigest()


class CodeTool(FunctionTool):
    def __init__(self, plugin, name):
        params = copy.deepcopy(PARAMETERS[name])
        if name == "code_start":
            params["properties"]["quota"]["maximum"] = plugin.jobs.max_calls
            params["properties"]["quota"]["default"] = min(50, plugin.jobs.max_calls)
            params["properties"]["quota"]["description"] = (
                "受代码插件和 API 插件管理员上限共同约束；重载后生效。失败尝试也消耗额度。"
            )
        super().__init__(name=name, description=DESCRIPTIONS[name], parameters=params)
        self.plugin = plugin

    async def call(self, context, **kwargs):
        try:
            if not self.active or self.plugin.closed or not self.plugin.config.get("enabled", True):
                raise ValueError("代码执行已关闭")
            from jsonschema import validate

            validate(kwargs, self.parameters)
            event, owner = identity(context)
            jobs = self.plugin.jobs
            if self.name == "code_start":
                inputs = {}

                def add(name, data):
                    filename(name)
                    if name in inputs:
                        raise ValueError("输入文件名重复")
                    if (
                        len(inputs) >= 20
                        or sum(map(len, inputs.values())) + len(data) > 8 * 1024 * 1024
                    ):
                        raise ValueError("输入文件超过限额")
                    inputs[name] = data

                for name, text in kwargs.get("input_texts", {}).items():
                    add(name, text.encode())
                files = [c for c in event.get_messages() if isinstance(c, File)]
                for index in kwargs.get("attachment_indexes", []):
                    if index >= len(files):
                        raise ValueError("附件序号不存在")
                    component = files[index]
                    path = Path(await component.get_file())
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or path.stat().st_size > 8 * 1024 * 1024
                    ):
                        raise ValueError("附件无效或过大")
                    add(component.name or f"attachment-{index}", path.read_bytes())
                for entry in kwargs.get("previous_files", []):
                    path = jobs.artifact(entry["job_id"], entry["name"], owner)
                    add(entry["name"], path.read_bytes())
                result = jobs.start(
                    owner,
                    kwargs["code"],
                    kwargs.get("api_scopes", {}),
                    inputs,
                    kwargs.get("timeout", 120),
                    kwargs.get("quota", min(50, jobs.max_calls)),
                )
            elif self.name == "code_status":
                record = jobs.get(kwargs["job_id"], owner)
                pending = jobs.tasks.get(kwargs["job_id"])
                if pending and kwargs.get("wait_seconds", 10):
                    await asyncio.wait([pending], timeout=kwargs.get("wait_seconds", 10))
                result = jobs.view(record)
            elif self.name == "code_stop":
                result = await jobs.stop(kwargs["job_id"], owner)
            else:
                path = jobs.artifact(kwargs["job_id"], kwargs["name"], owner)
                await event.send(event.chain_result([File(name=kwargs["name"], file=str(path))]))
                result = {"sent": True, "name": kwargs["name"]}
            result = {"ok": True, **result}
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc)[:300] if type(exc) is ValueError else "参数无效或执行服务不可用",
            }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
            isError=not result["ok"],
        )


class CodeRunningPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config, self.closed, self.tools = config, False, []
        root = StarTools.get_data_dir("astrbot_plugin_code_running")
        self.jobs = Jobs(
            root / "jobs",
            str(root / "runner.sock"),
            str(root.parent / "astrbot_plugin_api_import" / "task-gateway.sock"),
            max_calls=config.get("max_calls", 200),
        )
        self.web_handlers = [self.page_jobs, self.page_stop]
        context.register_web_api(
            "/astrbot_plugin_code_running/jobs", self.page_jobs, ["GET"], "代码执行管理"
        )
        context.register_web_api(
            "/astrbot_plugin_code_running/stop", self.page_stop, ["POST"], "代码执行管理"
        )

    async def initialize(self):
        manager = self.context.get_llm_tool_manager()
        if any(t.name in PARAMETERS for t in manager.func_list):
            raise ValueError("代码执行工具名称冲突")
        self.tools = [CodeTool(self, name) for name in PARAMETERS]
        self.context.add_llm_tools(*self.tools)

    async def page_jobs(self):
        self.jobs.prune()
        return json_response(
            {
                "max_calls": self.jobs.max_calls,
                "jobs": [self.jobs.view(r) for r in self.jobs.records.values()],
            }
        )

    async def page_stop(self):
        try:
            payload = await request.json()
            return json_response(await self.jobs.stop(payload["job_id"]))
        except (KeyError, TypeError, ValueError):
            return error_response("任务不存在或请求无效")

    async def terminate(self):
        self.closed = True
        await self.jobs.close()
        manager = self.context.get_llm_tool_manager()
        owned = {id(t) for t in self.tools}
        manager.func_list[:] = [t for t in manager.func_list if id(t) not in owned]
        self.context.registered_web_apis[:] = [
            r for r in self.context.registered_web_apis if r[1] not in self.web_handlers
        ]
