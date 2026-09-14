# 文件与恢复

输入位于 input/，输出写 output/ 顶层；工作目录其他文件不会自动导出。checkpoint 保存文件不必写 output/。输入/输出各最多 20 个文件，总计各 8 MiB；系统文件另有有界配额。标准输出最多 64000 字符，不适合输出全部大文件。

submitted_code.py 自动保存本轮源程序。code_read(job_id, name, offset, limit) 可分段读取自己的 UTF-8 文本文件，不必启动 Python 或发消息；每次最多 8000 字符。api-results.jsonl 每行是 {sequence,audit,response}，其中 response 才是 API 响应；不要把日志外层当成供应商数据。api-audit.json 是调用记录，不是业务结果。系统文件名不可覆盖。

code_status 列出文件名；code_start.previous_files 使用同一会话用户的 job_id/name 将文件作为新任务 input/。因此可以复用程序、输入、响应或检查点后修改代码继续；不会恢复程序栈，不自动继承旧 API 授权。既有输入如需后续复用，显式复制到 output/ 或 checkpoint 保存。

任务及文件保留 24 小时、最多 50 个任务；失败也保留已确认保存的文件。只用当前任务实际生成且验证过的文件交付，不把旧成果或部分处理结果说成当前任务完成。code_file 会发送文件，按用户要求使用。
