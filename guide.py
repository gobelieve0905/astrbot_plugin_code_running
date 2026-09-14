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
