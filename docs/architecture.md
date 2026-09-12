# 架构

浏览器通过同源 HTTP API 发送消息和附件，通过 SSE 接收会话快照。FastAPI 为每个网页管理的执行创建一个 `CodexRPC` 连接，调用本机 `codex app-server --stdio`；审批请求由网页显式回应。对外服务始终使用单进程，不能通过多个 worker 扩容。

| 模块 | 职责 |
| --- | --- |
| `server/config.py` | 统一 TOML / 环境配置，路径与模型校验；导入不读取凭据、不创建运行文件 |
| `server/__main__.py` | 校验 CLI / 前端后启动单 worker Uvicorn |
| `scripts/relay.sh` / `relay_manager.py` | 标准库管理入口：路径识别、依赖准备、用户服务、诊断与失败恢复 |
| `server/maintenance.py` | 管理器和 HTTP 写请求共享的维护锁、只读任务状态检查 |
| `server/app.py` | API、认证、会话适配、队列与目标生命周期、SSE |
| `server/storage.py` | Relay SQLite schema 的幂等升级、连接、口令创建 |
| `server/codex_rpc.py` | 双向 JSON-RPC、请求关联、通知队列、显式审批 |
| `server/directories.py` | 真实目录浏览、自然排序、搜索、分页与路径边界 |
| `server/media.py` / `files.py` | 图片/文档校验、不可猜测附件 ID、持久化、认证下载 |
| `src/main.jsx` | 页面状态、会话切换、消息流、发送与命令交互 |
| `src/sessionState.js` | 按会话保存草稿、发送/停止状态与错误，异步更新保留原会话归属 |
| `src/MessageQueue.jsx` | 待发送列表、逐条删除与过期推送处理 |
| `src/ModelPicker.jsx` / `preferences.js` | 模型与强度菜单、选择校验、本地偏好读取 |
| `src/GoalBar.jsx` / `DirectoryPicker.jsx` | 目标控制及目录浏览 |
| `src/MessageBody.jsx` / `commands.js` | Markdown/数学渲染及命令展示语义 |
| `src/Images.jsx` / `SketchPad.jsx` | 文件交互、图片预览及画板 |

## 数据边界

- Codex 管理 `CODEX_HOME` 中的会话数据库、rollout、目标、登录和 provider 配置。Relay 只读数据库及 rollout，目标/归档/重命名等变更通过原生 RPC 完成。
- Relay 在 `data_dir` 管理自己的 SQLite 任务、队列、指令记录和附件索引，不将数据混入源码目录。
- 首次安装没有 `state_5.sqlite` 时返回空列表；已有数据库 schema 不兼容时返回 503，不假装历史已消失。
- `state_5.sqlite`、`sessions/`、writer lock 与目标协议是版本敏感的适配边界。升级 Codex 时重新验证，不能仅凭网页能打开判断兼容。
- 重启后原有执行标记为脱离管理，不重新提交原始任务或盲目执行待处理队列。上线前应主动结束执行。

## 功能约束

网页管理的任务可以停止和审批；被外部 IDE 占用的会话通过 CLI 队列追加纯文字，不能从网页接管其模型、图片、审批和停止。目标自动继续沿用启动时的模型、强度与权限。

初始消息接口和 SSE 只传最近 10 条合并记录；按需加载完整历史并保持滚动位置。仅显示用户消息、对外回复和工具摘要，过滤内部推理及系统内容。

目标栏、队列和审批组件按会话独立挂载，兄弟组件使用不同标识。每次切换会话使旧页面请求失效，包括切走后再返回同一会话；新的推送或读取请求也会使更早的读取结果失效。发送回执只清理已提交的草稿和附件，保留请求期间的新输入。

工作目录中的返回文件被复制到附件存储，认证接口按 ID 返回，浏览器不会得到任意路径读取入口。图片允许受限预览，文档作为下载附件返回。KaTeX 禁用可信命令，Markdown 不执行原始 HTML。
