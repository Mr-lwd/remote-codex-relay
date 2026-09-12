# 配置参考

配置入口是项目根目录的 `relay.toml`，模板为 `relay.example.toml`。使用 TOML，不自动加载 `.env`，也不把生图或对话 API 密钥写入项目配置。优先级：**环境变量 > TOML > 内置默认值**。

`RELAY_CONFIG_FILE` 可指向其他 TOML 文件；相对路径以项目根目录解析，文件内的相对路径以该 TOML 所在目录解析，`~` 以运行用户主目录展开。显式指定不存在的配置、未知字段、无效端口、不存在的工作区、越界默认目录等会在启动时直接报错。

| TOML 键 | 环境变量 | 默认值 |
| --- | --- | --- |
| `server.host` | `RELAY_HOST` | `127.0.0.1` |
| `server.port` | `RELAY_PORT` | `8000` |
| `server.secure_cookie` | `RELAY_SECURE_COOKIE` | `false`；HTTPS 代理后设置 `true` |
| `paths.data_dir` | `RELAY_DATA_DIR` | 项目 `runtime/` |
| `paths.dist_dir` | `RELAY_DIST_DIR` | 项目 `dist/` |
| `paths.workspace_root` | `RELAY_WORKSPACE_ROOT` | 当前用户主目录 |
| `paths.default_cwd` | `RELAY_DEFAULT_CWD` | 项目目录；项目不在工作区内时使用工作区根目录 |
| `codex.binary` | `CODEX_BIN` | 从 `PATH` 查找 `codex` |
| `codex.home` | `CODEX_HOME` | 当前用户 `~/.codex` |
| `ui.default_model` | `RELAY_DEFAULT_MODEL` | `ui.models` 第一项 ID |
| `ui.default_effort` | `RELAY_DEFAULT_EFFORT` | `medium` |
| `ui.models` | 无 | Sol、Astra 两项；每项必须有 `id` 和 `name` |

主目录和可执行路径改为通用默认值；已有部署若使用 `CODEX_BIN`、`CODEX_HOME`、`RELAY_DATA_DIR`、`RELAY_DIST_DIR` 环境变量，继续兼容。TOML 中的 Codex home 会传给所有原生子进程，不修改用户的全局 Codex 配置。

启动器 `.venv/bin/python -m server --host ... --port ...` 的命令行参数优先于对应监听配置。直接使用 `uvicorn server.app:app` 时，监听地址和端口由 Uvicorn 参数决定，`RELAY_HOST/PORT` 不控制 Uvicorn 命令行。

`server.host = "0.0.0.0"` 监听所有 IPv4 网卡，用于局域网或公网直连。客户端使用服务器实际 IP 或解析到它的域名访问；`0.0.0.0` 不是访问地址。修改后重启后端，并检查云平台安全组、主机防火墙是否放行配置端口。环境变量与启动参数可能覆盖 TOML 中的值，排查时应同时核对服务的启动配置。

HTTP 直连必须使用 `secure_cookie = false`；HTTPS 反向代理后使用 `true`，后端通常保持 `127.0.0.1`。域名解析不会自动提供 HTTPS，完整配置见[部署指南](deployment.md#nginx-和-https)。

## 模型配置

```toml
[ui]
default_model = "your-model-id"
default_effort = "medium"
models = [
  { id = "your-model-id", name = "My Model" },
]
```

设置自己的服务支持的 ID；配置文件不决定账号是否有权使用该模型。推理强度固定提供 `low/medium/high/xhigh/max`，服务需支持你选择的强度。浏览器按会话保存模型与强度；失效的模型选择回退到配置默认值。服务器从认证后的会话列表接口发送 UI 配置，无需重新构建前端；改配置后重启后端并刷新页面。

普通消息、排队消息、`/compact`、`/goal` 启动和继续均携带模型与强度。计划模式仅通过 `/plan` 显式启用，刷新页面回到执行模式；权限偏好仍保存。

## 工作目录、数据和口令

工作区根目录与默认目录必须预先创建。目录浏览与新建任务验证真实路径，符号链接不能越出配置边界。该边界用于目录选择与新任务创建；已存在的 Codex 会话仍可能位于其他目录，不能把它视作账号隔离或替代 Codex 沙箱。

`data_dir` 中保存 `access.txt`、`relay.sqlite`、上传及返回的附件与索引、运行日志。改路径不会自动迁移旧数据。停服后复制完整目录并更新配置，保留权限与附件引用路径；跨主机迁移见部署说明。

默认随机生成访问口令，不输出到服务日志。要更换口令：结束任务、停服，替换 `access.txt` 为一行新口令（1–200 字符，建议至少 24 位随机值），权限设为 `600`，再启动。也可删除该文件，让下次启动重新生成。旧登录 Cookie 随口令变更失效。

登录 Cookie 名称按实例的数据目录隔离，避免同域名其他 Relay 实例或旧版 `relay_session` Cookie 干扰。更换数据目录或从旧版升级后需要重新登录；只修改监听端口不会改变 Cookie 名称。多个实例仍必须使用不同的数据目录。

## 图片生成

图片上传、预览、下载、绘画板无需图片 API。生成新图片由 Codex 的技能或工具执行，本项目不内置生图服务商或密钥。使用自己的 Codex skills/provider 配置；环境变量应仅作用于对应生图命令。不要将聊天 provider 变量用来切换图片服务，也不要把凭据提交到此仓库。

## 开发代理

`npm run dev` 默认在 Vite 端口提供前端，将 `/api` 代理到 `http://127.0.0.1:8000`。后端换端口时，在运行 Vite 前设置 `RELAY_DEV_BACKEND=http://127.0.0.1:端口`。这是开发工具配置，不会进入构建产物。
