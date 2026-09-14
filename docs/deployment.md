# 执行服务部署

2026-09-14。由服务器运维入口在备份与部署锁内安装，服务源码来自已推送 develop 固定提交。服务是宿主可信控制面，只有它调用 Docker；AstrBot 容器和不可信代码容器均不挂载 Docker socket。

服务运行 Python 3.10+，入口 runner/service.py，环境 `CODE_RUNNING_IMAGE=sha256:...`、`CODE_RUNNING_SOCKET=/srv/apps/astrbot/state/data/plugin_data/astrbot_plugin_code_running/runner.sock`。根目录、程序和环境由 root 管理；socket 600，属主 997。生产 systemd 服务禁止读取应用凭据目录，使用专有启动路径。重启前只清理由 `astrbot.code-running=true` 标记的遗留任务容器。

全局一个任务，192 MiB 内存、不使用 swap、0.5 CPU、32 PID；/work 64 MiB、/tmp 16 MiB tmpfs。Docker 自带 seccomp/AppArmor 默认策略保留。镜像使用固定本地 ID且禁止拉取，不接收调用者指定镜像或容器参数。

插件的安装包只包含 AstrBot 运行文件、后台页面和用户文档；runner、tests、docs 由 .gitattributes export-ignore 排除。执行服务必须从固定 Git 提交源码部署，不从模型生成的结果目录读取程序。回滚恢复上一个服务程序、镜像环境和插件版本；不删除任务审计/用户文件。

检查先验证四容器和单飞书连接，离线测试后创建新加密备份，获取 `/run/astrbot-deploy.lock`。执行真实断网、隔离、停止和资源限制测试；插件热重载后检查工具注册与所有服务健康。测试使用合成数据，不请求业务接口、不发送聊天消息。现有 API 插件需具备 v1 任务网关。

首次分析镜像 ID：`sha256:d5cc8679b9420b220a941691e0e4d5997e6ac75ddea1ffb3e0ad81b3296f118d`，保存在 `runner/image-id.txt`。`runner/installed-packages.txt` 为实际构建结果清单；Dockerfile 固定基础镜像和四个主依赖，重建后须重新导出依赖清单、验证并更新镜像 ID，不能把浮动构建 tag 当生产版本。
