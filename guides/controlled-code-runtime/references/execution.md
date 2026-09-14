# 程序执行

code_start 接收完整 Python，立即返回任务 ID。服务器独立运行，code_status(wait_seconds=20) 可等待状态；循环和计算可留在程序内，不必逐项启动任务。方法与拆分粒度按数据规模、接口限制和用户要求决定。

from controlled_api import budget, checkpoint。budget() 返回 remaining/seconds_left；checkpoint(文件名, JSON值) 确认保存后返回。单次最长 600 秒；调用数受管理员上限和本次 quota 共同约束。任务运行时间、API 额度、AstrBot 的模型轮次数是不同限制，不能互相替代。

执行错误检查 code_status 的 error_code 和输出。参数错误先查看定义或预检；程序错误可用已有输入/响应修改代码重算。新任务需重新声明 API 范围，不能重放结果不明的写操作。权限拒绝应停止相关操作。后台不保证自动续跑、自动扩额或无限任务；同样失败且无进展不重复尝试。

已有任务运行时不并发启动新任务。code_stop 撤销通道，但已发送的外部写请求可能执行过，需依据接口幂等或状态查询核实。
