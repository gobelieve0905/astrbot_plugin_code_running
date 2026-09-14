# 检查点续接与恢复

1. 用 code_status 确认上一段状态、文件、错误码和预算。失败不代表没有数据；成功也不代表整体查全。
2. 选择同一会话用户的 progress.json 作为 code_start.previous_files 输入。新任务在 input/progress.json 读取；重新声明所需 api_scopes，后台会重新检查权限。不要把文件路径、凭据或旧任务授权当成可复用权限。
3. 先核对检查点查询指纹、目标集合及口径，继续未完成游标。数据和游标必须对应同一个已确认检查点；更换查询条件应建立新任务范围，不能直接拼接旧结果。
4. 完成若干段后，在纯计算任务中通过 previous_files 合并数据。该步骤使用空 api_scopes；没有理由再次请求所有接口。

```python
import json
from pathlib import Path

saved = json.loads(Path("input/progress.json").read_text())
# 加入 batch 章节的 run_segment 定义及已经核实的适配函数后：
# result = run_segment(targets, build_request, unpack, query_id, saved)
```

如果没有应用检查点，api-results.jsonl 中每行包含 sequence、audit 和 response。它是当前段已获得的响应，不是整个业务范围已完成的证明。可在新的纯计算任务读取，按 audit.tool、node_id、query_sha256、cursor_sha256 对照原查询计划恢复。游标和查询指纹是哈希，不能反推原参数；下一页位置需使用 response 的已确认分页元数据，并结合原计划。不得假定所有响应都含 data 或 page。

```python
import json
from pathlib import Path

entries = [json.loads(line) for line in
           Path("input/api-results.jsonl").read_text().splitlines() if line.strip()]
received = [e for e in entries if e["response"].get("ok") is True]
failed = [e["audit"] for e in entries if e["response"].get("ok") is not True]
print({"received_responses": len(received), "failed_attempts": len(failed)})
```

恢复时以检查点为已提交边界；日志中位于边界之后的只读响应需先匹配查询/游标并去重，再应用。无法证明对应关系时标记不确定；不能把日志行数当作目标数，或同时合并检查点与日志的相同记录。写操作结果不明时停止重放，使用接口支持的状态查询/幂等机制核实。

错误处理：QUOTA_EXHAUSTED → 保存并在合法预算内续段；EXECUTION_TIMEOUT/TASK_EXPIRED → 从已保存位置恢复，不反复重跑；PERMISSION_REVOKED/GATEWAY_REJECTED → 检查授权或参数，不绕过；RESULT_STORAGE_LIMIT → 保留已有文件、缩小下一段，不声称当前响应已保存；PYTHON_ERROR → 先改代码并用已有响应校验，不直接重复全部取数。同样错误再次出现且无进展时停下说明原因。

预算可配置不代表可无限执行。长任务按用户原范围推进，定期报告覆盖进度；若预计总规模异常大或需求变化需要额外高成本操作，先说明范围和资源约束。
