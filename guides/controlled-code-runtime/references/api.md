# API 工具契约

code_api(query=关键词, offset=0) 搜索当前可用工具，按 next_offset 继续获取；tool=真实名称取得精确定义。tool、arguments、constraints 同时提供可预检 JSON Schema 和顶层等值约束。预检不发请求，也不授权；平台专有字段、凭据、资源访问及实时权限仍在正式请求校验。

code_start.api_scopes 的键为真实工具名，值为固定参数约束。{工具名:{id:"A"}} 只允许该参数等于 A；不能在此任务调用 B。{工具名:{}} 不固定参数，可在用户授权范围内编程循环调用；不开放后台关闭的操作。不支持用 __2 等编造工具别名，也不能把数组理解为多值等值授权。api_scopes={} 是纯计算。

Python 中 from controlled_api import tools, call, APIError。tools 是本任务定义列表（name/parameters/constraints）。call(真实名称, **参数) 返回单次完整响应；ok/data/page 等为网关层，data 内结构由实际供应商定义，不要猜测或递归解码。调用失败抛 APIError，含 code/response。参数名、层级及类型按定义填写。每次请求重新检查权限，失败尝试也消耗额度，没有自动重试或分页。

拒绝访问不能通过搜索、预检、续任务、换名称或另选网络入口绕过。查看接口不会返回 Token 或代理凭据。
