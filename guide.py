"""Read-only, fixed inventory of shipped execution guidance; never arbitrary files."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).parent / "guides" / "batch-api-tasks"
SECTIONS = {
    "overview": "SKILL.md",
    "batch": "references/batch.md",
    "resume": "references/resume.md",
    "analysis": "references/analysis.md",
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


ROUTING = """[代码执行插件：通用批量任务指南]
遇到多账户、多页、跨数据源取数或复杂文件聚合，先调用 code_guide(section="overview")；编写批量代码前读 batch，断点续接读 resume，聚合交付前读 analysis。核对工具、预算、分页、检查点和覆盖率，在已授权任务范围内继续未完成的只读分段，避免从头重复查询。简单单次查询不必使用批量流程。
code_guide 是固定只读指南，code_start 是独立隔离执行服务，不需要开启原生 Computer Use 或宿主终端。权限与固定代理由 API 插件校验，不得绕过，不自动重放写操作。代码执行成功不等于范围查全；交付部分结果必须明确标注。"""
