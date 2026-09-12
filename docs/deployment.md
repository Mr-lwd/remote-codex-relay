# 部署、升级和备份

先按 README 完成安装与登录验收，再部署常驻服务。服务只运行一个 worker，多个实例不可共用同一运行目录。建议由普通用户运行，服务器可访问范围由该用户与 Codex 权限共同决定。

## systemd 用户服务

推荐由统一管理命令生成当前目录的服务配置：

```bash
bash scripts/relay.sh start
bash scripts/relay.sh status
bash scripts/relay.sh logs
# 需要用户服务自启动时显式设置：
bash scripts/relay.sh start --enable
```

生成的单元位于 `~/.config/systemd/user/codex-relay.service`（遵循 `XDG_CONFIG_HOME`），包含实例标识、正确转义的路径及运行环境。首次可接管本项目原有的标准 Python 启动单元；其他项目的同名服务、自定义启动命令、EnvironmentFile 或 drop-in 配置会提示人工核对，不直接覆盖。

Node 查找顺序为当前 PATH、已有服务 PATH，以及用户目录内已安装的 Node/nvm/mise 版本，只接受 22/24。管理器保留原服务环境并应用当前的 Relay/Codex 配置覆盖，生成的环境文件只允许当前用户读写。不会加载 shell 初始化脚本或更改 Codex 登录。

需要退出 SSH 后继续运行并随服务器启动，可由管理员执行 `sudo loginctl enable-linger "$USER"`。更改配置后使用 `bash scripts/relay.sh restart`。没有用户 systemd 或使用 `start --foreground` 时在前台运行；可在另一个终端执行 `stop`，或在前台按 Ctrl+C。前台模式的安装记录会使后续管理命令继续管理同一个前台实例。

高级用户仍可使用 `deploy/codex-relay.service.example` 手动安装服务；自行维护其绝对路径与 PATH。直接使用 systemctl 或 Ctrl+C 不会经过管理器的任务检查，应自行等待任务结束。

## Nginx 和 HTTPS

以 Debian/Ubuntu 为例：安装 `nginx certbot python3-certbot-nginx`；域名 A/AAAA 指向真实主机，开放 80/443。只有配置了可用 IPv6 才添加 AAAA。

1. 复制 `deploy/nginx.conf.example` 到 `/etc/nginx/sites-available/codex-relay`，替换 `console.example.com`，必要时改后端端口。
2. 将该文件链接到 `/etc/nginx/sites-enabled/codex-relay`，运行 `sudo nginx -t`，通过后 reload Nginx。
3. 执行 `sudo certbot --nginx -d 你的域名 --redirect`，按提示申请证书。自动续期通过 `sudo certbot renew --dry-run` 验证，并检查 `certbot.timer`。
4. `relay.toml` 中保留 `host = "127.0.0.1"`，设置 `secure_cookie = true`，空闲时重启应用。从 HTTPS 地址登录。

代理关闭缓冲和缓存，否则 SSE 会延迟；52 MB 请求上限允许应用接收 50 MB 文档。模板不包含实际域名、主机地址或证书路径。Uvicorn 默认不信任转发头，Secure Cookie 由 Relay 显式配置；反代后的应用登录限流按代理地址共用（每分钟 10 次失败），不是独立用户限流。

上线后检查首页、登录、实时连接、图片/PDF 上传下载以及手机界面。`/api/health` 仅证明服务存活和功能版本，不能证明 Codex 登录、模型额度或原生目标接口可用。

## 更新与回滚

1. 在网页暂停所有目标，等待当前执行及队列结束。停服务。备份现有代码版本、构建产物、配置与完整运行数据。
2. 更新源码，运行相应测试，再执行 `bash scripts/relay.sh repair`。管理器按依赖与源码指纹决定是否安装和构建，配置与依赖未变化时不重复下载。
3. 服务恢复后重新验证登录、真实会话及收发附件。不要对任务仍在运行的服务使用开发热重载。
4. 失败时停服，恢复旧代码、配置、依赖锁对应的环境及备份数据，再启动。不要仅回滚前端来掩盖数据库或协议不兼容。

旧浏览器标签可能持有旧的静态资源文件名，升级后请刷新页面。第一次采用此发布版时无需更改原有运行目录；新增配置有通用默认值，原环境变量继续生效。

## 备份和迁移

同机改名或移动项目时，先执行 `bash scripts/relay.sh stop`，移动完整项目后在新目录执行 `bash scripts/relay.sh repair`。不依赖原 `.venv` 是否可用；启动脚本使用系统 Python 识别自己的真实位置。相对配置随项目移动；指向旧项目内部的绝对路径会在验证、备份后更新，项目外部的路径保持原值。路径重写可能重新排版 TOML，原文件保留在备份中。

`runtime/manager.json` 记录原安装位置、实例标识和依赖指纹，需随项目保留。首次修复没有管理记录的安装时参考虚拟环境与现有单元；证据冲突、旧目录仍存在时停止，不猜测或扫描磁盘接管项目。管理记录与安装日志固定保存在项目 `runtime/`，应用数据仍使用 `paths.data_dir`。

被替换文件或目录的备份放在原位置旁，名称为 `.原名称.relay-backup-随机值`。失败产物保留为 `.原名称.relay-failed-随机值`，原文件会恢复；失败详情见 `runtime/manager.log`。确认修复完成后可自行清理这些备份以释放空间。系统服务日志使用 journal，前台服务日志使用 `data_dir/server.log`，`logs` 命令会选择对应来源。

维护命令使用当前用户状态目录中的互斥锁，并在读取任务状态到服务恢复期间阻止新的 HTTP 写请求。存在执行任务、排队消息或执行指令时拒绝停止/重启；进程已经异常退出但数据库仍标记执行中时也会保守拒绝，应先核对执行现场。不会清空队列、自动改写原生历史或结束占用端口的其他程序。

停服后备份 `relay.toml`、整个 `data_dir`（含口令、Relay 数据库、媒体索引和附件）、相关工作目录，以及 Codex 自己的 home/会话。它们含个人内容和凭据，必须存入私有备份，不上传源码仓库。

复制到新机器时保持运行用户权限和原有绝对路径更稳妥：Codex rollout、工作目录和排队附件引用包含绝对路径。本项目没有跨路径数据库迁移器。若无法保持路径，先按 Codex 的迁移方式处理会话，再验证每个旧会话；全新安装可直接使用新目录。

恢复 `access.txt` 会保留原访问口令；需使旧浏览器登录失效时更换口令。不要在服务仍写入时直接复制 SQLite 文件来代替一致性备份。

## 常见问题

- **本机能打开，其他设备打不开**：HTTP 直连时设置 `host = "0.0.0.0"` 并重启；浏览器使用实际 IP 或域名及端口。检查 A/AAAA 解析、云平台安全组和主机防火墙，确认 TCP 端口已放行。HTTPS 反代部署则检查代理的 80/443 端口。
- **找不到 Codex**：先 `command -v codex`，设置 `CODEX_BIN` 或 `codex.binary`；systemd 还须能找到 Node。
- **503 / 无法读取会话数据库**：执行 `scripts/doctor.py`。已有 `state_5.sqlite` 的 schema 不匹配时核对 CLI 版本，不要删除个人数据库试错。
- **网页能打开但模型失败**：检查本机 Codex 登录、服务商、可用模型/强度及额度。UI 模型 ID 是可配置的。
- **输入口令后仍停在登录页或立即掉线**：按下方[登录排查](#登录排查)检查访问协议、Cookie 与服务响应。
- **实时消息卡住**：检查 Nginx/CDN 的 SSE 缓冲和超时设置。
- **较大附件上传 500，小文件正常**：检查 Nginx 错误日志是否有上传缓存目录 `Permission denied`。确认主配置的 worker 用户，将对应缓存目录属主恢复为该用户并保留原权限；独立模板测试须使用自己的缓存路径。
- **前端未构建或移动后首页 503**：在当前项目目录执行 `bash scripts/relay.sh repair`；HTML 故障页会显示该命令。使用 `status` 区分构建缺失与旧安装路径。
- **端口占用**：使用 `status` 检查监听地址，调整自己的配置或自行处理占用程序；管理器不会结束外部进程或自动换端口。
- **缺少 Python venv 或 Node**：按命令给出的安装提示补齐系统环境后重试。项目依赖会自动安装，系统包不自动安装。
- **依赖安装失败**：确认 Python/Node 版本、网络与包索引。仓库未指定任何私有镜像。

### 登录排查

1. 确认使用当前 `data_dir/access.txt` 中的口令。`POST /api/login` 返回 401 表示口令不匹配，429 表示失败次数过多，应等待一分钟再试。
2. 若登录返回 200，随后 `GET /api/threads` 返回 401，说明口令已通过，但请求没有携带有效的登录 Cookie。查看浏览器开发者工具的 Network / Cookies 面板中 `Set-Cookie` 的拦截原因；不要公开口令或 Cookie 值。
3. 通过 `http://IP:端口/` 或 `http://域名:端口/` 直连时设置 `secure_cookie = false`，并检查是否有 `RELAY_SECURE_COOKIE` 环境变量覆盖。`true` 仅配合 HTTPS 访问；改配置后重启服务。
4. 本版使用实例独立的 Cookie 名称；浏览器 Cookie 不按端口隔离，旧版同名 Secure Cookie 可能阻止 HTTP 页面覆盖，浏览器会报告 `OverwriteSecure`。升级后强制刷新前端并重新登录；仍有冲突时清除对应站点的 Cookie。
5. 如果浏览器禁用了站点 Cookie，允许该站点存储 Cookie，并直接在浏览器标签页打开工作台。页面会明确提示“口令已验证，但浏览器未能保存或发送登录 Cookie”。IP 与域名保存各自的登录状态，切换访问地址后需分别登录。
