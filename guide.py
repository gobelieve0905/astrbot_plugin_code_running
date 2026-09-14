"""Read-only, fixed inventory of shipped execution guidance; never arbitrary files."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).parent / "guides" / "controlled-code-runtime"
SECTIONS = {
    "overview": "SKILL.md",
    "api": "references/api.md",
    "execution": "references/execution.md",
    "artifacts": "references/artifacts.md",
}


def read_guide(section):
    if section not in SECTIONS:
        raise ValueError("请选择固定的指南章节")
    path = ROOT / SECTIONS[section]
    if any(p.is_symlink() for p in (ROOT, path.parent, path)) or not path.is_file():
        raise ValueError("指南文件不可用")
    if path.stat().st_size > 32000:
        raise ValueError("指南文件超限")
    raw = path.read_bytes()
    return {
        "section": section,
        "content": raw.decode("utf-8"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


ROUTING = """[代码执行插件：环境说明]
可使用隔离 Python 执行程序，按任务需要选择直接调用工具或编程。需要确认 API 名称、参数及任务约束时使用 code_api 搜索、查看定义和预检，不猜测工具别名。需要了解运行环境、文件或恢复方式时按需阅读 code_guide。后台任务通过 code_status 等待，不用每次循环都回到模型；错误后可修改程序并复用已保存文件。任务方法与完成标准由用户要求决定。工具预检与代码成功不代表用户任务完成；API 权限和固定代理由 API 插件强制执行，不绕过，不自动重放结果不明的写请求。"""
