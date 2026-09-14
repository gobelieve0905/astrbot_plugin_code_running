# Python 代码执行

当前版本：0.1.0。独立 AstrBot 插件，支持 Python 批量任务、文件处理、计算、数据分析和结果文件。用户直接提出问题，Agent 选择 API 工具或编写 Python，无需选择工作流。

安装插件后，管理员还须安装独立执行服务。缺少执行服务时任务明确失败，绝不退回宿主机执行。API 插件可继续单独使用；未安装 API 插件时，`api_scopes={}` 的文件与计算任务仍可运行。

## 使用

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

文件写入 `output/` 顶层，输入位于 `input/`。最多 20 个输入/输出文件，各总计 8 MiB；单任务最多 600 秒、200 次网关调用、64,000 字符日志。整台执行服务只同时运行一个任务，不自动排队。记录和文件保留 24 小时，最多 50 个任务，在访问和提交时清理。重载后未结束任务标记为中断，不恢复执行。

## 安全与部署

代码运行于独立的非 root Docker 容器：无网络、无宿主目录挂载、只读根文件系统、去除全部 capabilities、禁止提权，并限制 CPU/内存/进程/临时空间。Docker 是共享内核隔离，不是虚拟机；宿主管理员及可信插件进程属于信任边界。参见 [Docker 安全说明](https://docs.docker.com/engine/security/)。

执行服务通过固定的 Unix socket 接收代码，容器经标准输入输出进行受限 JSON RPC。平台 Token 和代理配置只在 API 插件，任务凭证保留在插件侧，不进入 Python。客户端不能指定镜像、网络、挂载、环境变量或 Docker 参数。固定 Meta 代理沿用 API 插件，不切换或回落直连。

生产部署须确认 AstrBot `computer_use_runtime=none`，停用可读宿主文件、任意联网、执行终端的其他插件/MCP，保留 API 插件为业务请求唯一入口。后台配置变动后须重新检查该边界，不能仅凭本插件隔离推断其他工具也安全。当前部署验收不发送消息、不查询业务 API。

执行服务默认只提供镜像预装的依赖，任务不能联网安装依赖。部署固定镜像与构建清单见 [执行服务部署说明](https://github.com/gobelieve0905/astrbot_plugin_code_running/blob/develop/docs/deployment.md)。开发测试和服务运维资料不进入插件市场安装 ZIP。
