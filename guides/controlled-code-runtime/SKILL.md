---
name: controlled-code-runtime
description: 隔离代码执行环境的工具契约、限制、文件与恢复说明；按需要阅读，不规定业务处理流程。
---
# 代码执行环境

这是插件内置环境手册，经 code_guide 读取，不要求原生 Computer Use 或终端。

- code_api：搜索已启用 API、查看定义、预检参数。详见 api 章节。
- code_start：提交完整 Python、输入文件和任务范围；返回任务 ID。详见 execution。
- code_status：等待并检查状态、日志和结果；code_stop 停止任务。
- code_file：发送当前会话用户的结果文件。文件引用与恢复详见 artifacts。

根据用户的问题选择方法；这里不要求查询特定平台、业务对象、固定步骤或输出格式。执行成功仅表示程序结束，结果是否满足用户要求仍须验证。API 插件可以单独使用，代码执行也可在 api_scopes={} 时独立进行计算。

运行服务无网络、无宿主目录访问；Token 和代理保留在 API 插件。管理员需部署独立执行服务。未安装或关闭时明确失败，不退回宿主执行。
