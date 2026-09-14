# 本地任务协议 v1

2026-09-14。接口是换行分隔 JSON，UTF-8，每帧最多 12 MiB。Unix socket 限同 UID；不是面向模型的通用网络代理，不提供 URL 请求、配置读取或权限管理方法。

API 网关位置：`data/plugin_data/astrbot_plugin_api_import/task-gateway.sock`。
1. `{"action":"issue","scopes":{"工具名":{"固定参数":"值"}},"ttl":120,"quota":50}` → credential、tools。
2. 同连接 `{"action":"call","credential":"...","tool":"工具名","arguments":{}}` → 完整单页结果，移除路径及敏感字段/配置凭据。
3. revoke 或断开连接销毁凭证。凭证仅当前连接有效；重载/修改操作使旧任务授权失效。每次调用及排队后重查权限/有效期，不自动重试。

代码插件只读取接口响应。API 插件实现接口并负责签发凭证、执行和脱敏，双方不互相导入代码。任务约束是选定操作和参数值限制，后台授权上限仍由 API 插件管理。

执行服务位置：`data/plugin_data/astrbot_plugin_code_running/runner.sock`。
首帧 `{code,inputs,tools,timeout}`；inputs 是文件名到 Base64。返回 output、api、file、done 或 error。api 事件只能包含 tool 和 arguments，由可信代码插件携任务凭证转交 API 网关；Python 无权申请新凭证。伪造事件不会提升权限。files 由插件校验名称、总大小和 SHA256，不信任容器路径。断连、超时、非法帧或服务停止均回收本次容器。
