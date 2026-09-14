# Python 代码执行

当前版本：0.1.1。独立 AstrBot 插件，支持 Python 批量任务、文件处理、计算、数据分析和结果文件。用户直接提出问题，Agent 选择 API 工具或编写 Python，无需选择工作流。

安装插件后，管理员还须安装独立执行服务。缺少执行服务时任务明确失败，绝不退回宿主机执行。API 插件可继续单独使用；未安装 API 插件时，`api_scopes={}` 的文件与计算任务仍可运行。

## 使用

- `code_guide`：按需读取通用批量任务 Skill（overview / batch / resume / analysis），规划预算、分页、续接及聚合校验；无需开启原生 Computer Use。
- `code_start`：启动任务，立即返回 ID；支持当前消息文件附件序号、文本文件、当前会话用户之前生成的文件。
- `code_status`：查看进度、标准输出、错误、调用审计和结果文件。
- `code_stop`：停止任务、关闭任务网关连接并触发容器回收。
- `code_file`：发送指定结果文件到当前会话。
- 后台插件页面「代码执行任务」：查看所有任务并停止运行中的任务。

示例代码（普通 CSV 统计，不需要 API 插件）：

```python
import csv
from pathlib import Path

rows = list(csv.DictReader(Path("input/data.csv").open()))
print("行数:", len(rows))
Path("output/result.txt").write_text(f"共 {len(rows)} 行", encoding="utf-8")
```

API 批量处理使用 `from controlled_api import call, tools`。`tools` 只包含任务授权的接口说明；`call('后台工具名', **参数)` 返回完整的**单页**结果，包括 `data`、`page`。授权通过 `code_start.api_scopes` 指定工具名及顶层参数固定值，例如 `{"Meta_get_insights":{"node_id":"act_123"}}`。空约束 `{}` 允许该操作的全部合法参数，管理员最终权限仍由 API 插件控制。模型应按任务需求缩小范围；这不是每位聊天用户的独立后台账户 ACL。

分页需要 Python 根据每页游标继续调用；每次分页、重试和关联调用均重新检查权限并消耗额度。失败不会隐式重试，写操作的远端结果可能未知。代码失败后可修改代码创建新任务，通过 `previous_files` 引用已生成文件。

文件写入 `output/` 顶层，输入位于 `input/`。最多 20 个输入/输出文件，各总计 8 MiB；单任务最多 600 秒，网关调用默认上限 200 次（由管理员配置）、64,000 字符日志。整台执行服务只同时运行一个任务，不自动排队。记录和文件保留 24 小时，最多 50 个任务，在访问和提交时清理。重载后未结束任务标记为中断，不恢复执行。

## 安全与部署

代码运行于独立的非 root Docker 容器：无网络、无宿主目录挂载、只读根文件系统、去除全部 capabilities、禁止提权，并限制 CPU/内存/进程/临时空间。Docker 是共享内核隔离，不是虚拟机；宿主管理员及可信插件进程属于信任边界。参见 [Docker 安全说明](https://docs.docker.com/engine/security/)。

执行服务通过固定的 Unix socket 接收代码，容器经标准输入输出进行受限 JSON RPC。平台 Token 和代理配置只在 API 插件，任务凭证保留在插件侧，不进入 Python。客户端不能指定镜像、网络、挂载、环境变量或 Docker 参数。固定 Meta 代理沿用 API 插件，不切换或回落直连。

生产部署须确认 AstrBot `computer_use_runtime=none`，停用可读宿主文件、任意联网、执行终端的其他插件/MCP，保留 API 插件为业务请求唯一入口。后台配置变动后须重新检查该边界，不能仅凭本插件隔离推断其他工具也安全。当前部署验收不发送消息、不查询业务 API。

执行服务默认只提供镜像预装的依赖，任务不能联网安装依赖。部署固定镜像与构建清单见 [执行服务部署说明](https://github.com/gobelieve0905/astrbot_plugin_code_running/blob/develop/docs/deployment.md)。开发测试和服务运维资料不进入插件市场安装 ZIP。


### 任务预算与续接

后台配置 `max_calls` 设置每任务最大调用数（1–10000，默认 200，重载生效），API 插件的 `task_max_calls` 同时限制授权。提高数量不会开放任何 API 操作；600 秒时间上限保持不变。

`controlled_api.call` 返回完整单页，失败抛 `APIError`，可检查 `.code` 和 `.response`。`budget()` 返回剩余调用数和秒数。没有自动分页或重试，尤其不能自动重放写操作。

每次受控调用完成后，结果先存入 `api-results.jsonl` 再交给代码；日志最多 7 MiB，超限明确失败，远端请求可能已成功。完整审计在 `api-audit.json`，业务数据不进入审计参数，游标/查询采用哈希。敏感 API 凭据由网关脱敏。任务输出与检查点有总大小限制。

```python
from controlled_api import checkpoint, budget
checkpoint("progress.json", {"next_account": 12, "after": "保存当前游标"})
print(budget())
```

检查点确认写入后才返回；避免与 `output/` 的最终文件重名。任务失败、超时或停止后，已保存结果和检查点仍在，可在 `code_start.previous_files` 中选取同一会话用户的文件，作为新任务 `input/` 输入。新任务必须重新声明 `api_scopes` 并通过当前后台权限检查；没有自动恢复程序栈、自动提额或自动重试。任务与文件保留策略仍为 24 小时、最多 50 个任务。

任务成功只表示代码执行完成。业务覆盖情况需核对审计、检查点与预期账户/分页清单；不自动保证查全。

### 通用批量任务指南

指南随插件发布在 `guides/batch-api-tasks/`，通过固定只读工具 `code_guide` 加载，不依赖 AstrBot 原生 Skill 的终端读取。Agent 在复杂批量任务前读取 overview，编码、续接和聚合时按需读取对应章节。管理员可在使用代码工具的人格提示中加入：

> 遇到多账户、多页或跨来源批量任务，先调用 code_guide(section="overview")；编码前读 batch，断点续接读 resume，聚合交付前读 analysis。按指南规划预算、保存数据与进度、续接未完成范围，并核实覆盖率。简单查询不必走批量流程。

指南是执行建议，不是权限控制。每次业务请求仍由 API 插件检查当前权限、任务范围和固定代理；指南工具不新增业务权限、网络或宿主文件访问。示例只演示通用组合方式，真实工具、字段与分页结构必须按对应接口确认。
