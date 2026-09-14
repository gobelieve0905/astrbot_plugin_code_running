# 批量读取示例

这是**只读**任务的组合模式，不是某个平台预置操作。先核对真实工具和响应；不要直接运行占位工具名。`code_start.api_scopes` 只声明需要的已启用操作。完整读取账户发现本身的所有分页，再去重建立 targets，不能只取前两页或按账户名称排除项目。

下面函数可放进 code_start 代码。调用方提供三个适配函数：
- build_request(target, cursor) → (真实工具名, 参数字典)，其字段按真实接口定义构造；
- unpack(response) → (数据行列表, 是否还有页, 下一游标)，优先使用插件已确认的分页元数据；
- query_id 是稳定的查询指纹，覆盖数据源、目标集合、时间、字段、筛选与口径，变更查询不续用旧检查点。

示例把数据与进度一起保存在 JSON，仅适合能放入文件上限的数据量。大数据每段保存独立文件，在最终纯计算任务中合并；不要让累计检查点无限增长。reserve_seconds 默认 60 秒，使用前按接口调整，至少覆盖当前 API 请求超时和一次保存；code_start.timeout 必须大于该预留时间。

```python
from controlled_api import call, checkpoint, budget, APIError

def run_segment(targets, build_request, unpack, query_id, saved=None, reserve_seconds=60):
    s = saved or {"query_id": query_id, "index": 0, "cursor": None,
                  "seen": [], "rows": [], "completed": [], "failure": None}
    if s["query_id"] != query_id:
        raise ValueError("查询已变化，不能沿用旧进度")
    if s.get("failure"):
        raise ValueError("先解决检查点中的失败，再决定补查范围")
    while s["index"] < len(targets):
        b = budget()
        if b["remaining"] < 1 or b["seconds_left"] < reserve_seconds:
            break
        target = targets[s["index"]]
        tool, arguments = build_request(target, s["cursor"])
        try:
            response = call(tool, **arguments)
        except APIError as exc:
            if exc.code not in {"QUOTA_EXHAUSTED", "TASK_EXPIRED"}:
                s["failure"] = {"target": target, "code": exc.code}
            break
        rows, more, cursor = unpack(response)
        if not isinstance(rows, list) or type(more) is not bool:
            s["failure"] = {"target": target, "code": "INVALID_PAGE"}
            break
        if more and (not cursor or cursor in s["seen"] or cursor == s["cursor"]):
            s["failure"] = {"target": target, "code": "INVALID_CURSOR"}
            break
        s["rows"].extend({"source": target, "row": row} for row in rows)
        if more:
            s["seen"].append(cursor)
            s["cursor"] = cursor
        else:
            s["completed"].append(target)
            s["index"] += 1
            s["cursor"], s["seen"] = None, []
        checkpoint("progress.json", s)
    checkpoint("progress.json", s)
    return {"complete": s["index"] == len(targets) and not s["failure"],
            "completed": len(s["completed"]), "expected": len(targets),
            "failure": s["failure"]}
```

读取响应后如解析代码异常，已取得的响应在 api-results.jsonl；修复解析再使用已有数据，不盲目重查。样例采用遇错停下，实际任务可设计逐目标失败清单继续其他只读目标，但不能把失败目标写成完成。
