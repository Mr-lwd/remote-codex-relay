# 部署、升级和备份

先按 README 完成安装与登录验收，再部署常驻服务。服务只运行一个 worker，多个实例不可共用同一运行目录。建议由普通用户运行，服务器可访问范围由该用户与 Codex 权限共同决定。

## systemd 用户服务

1. 将 `deploy/codex-relay.service.example` 复制为 `~/.config/systemd/user/codex-relay.service`（先创建目录）。
2. 替换模板中的两个 `/ABSOLUTE/PATH/codex-relay`。如果安装目录包含空格，用双引号包住 `WorkingDirectory` 的值与 `ExecStart` 的可执行文件路径。
3. 核对 `PATH`：非交互服务不会自动加载 `.bashrc`、nvm。把 Node 的目录加入模板的 PATH；Codex 可在 `relay.toml` 中配置为绝对路径。服务使用运行用户自己的 Codex home。
4. 启动服务：

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now codex-relay
   systemctl --user status codex-relay
   journalctl --user -u codex-relay -f
   ```

需要退出 SSH 后继续运行并随服务器启动，可由管理员执行 `sudo loginctl enable-linger "$USER"`。更改配置后，等待所有任务结束再 `systemctl --user restart codex-relay`。

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
2. 更新源码，运行 `bash scripts/setup.sh`，运行诊断及相应测试。安装脚本不会覆盖已有 `relay.toml`。
3. 启动服务并重新验证登录、真实会话及收发附件。不要对任务仍在运行的服务使用开发热重载。
4. 失败时停服，恢复旧代码、配置、依赖锁对应的环境及备份数据，再启动。不要仅回滚前端来掩盖数据库或协议不兼容。

旧浏览器标签可能持有旧的静态资源文件名，升级后请刷新页面。第一次采用此发布版时无需更改原有运行目录；新增配置有通用默认值，原环境变量继续生效。

## 备份和迁移

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
- **前端未构建**：运行 `npm ci && npm run build`，检查 `dist_dir`。
- **依赖安装失败**：确认 Python/Node 版本、网络与包索引。仓库未指定任何私有镜像。

### 登录排查

1. 确认使用当前 `data_dir/access.txt` 中的口令。`POST /api/login` 返回 401 表示口令不匹配，429 表示失败次数过多，应等待一分钟再试。
2. 若登录返回 200，随后 `GET /api/threads` 返回 401，说明口令已通过，但请求没有携带有效的登录 Cookie。查看浏览器开发者工具的 Network / Cookies 面板中 `Set-Cookie` 的拦截原因；不要公开口令或 Cookie 值。
3. 通过 `http://IP:端口/` 或 `http://域名:端口/` 直连时设置 `secure_cookie = false`，并检查是否有 `RELAY_SECURE_COOKIE` 环境变量覆盖。`true` 仅配合 HTTPS 访问；改配置后重启服务。
4. 本版使用实例独立的 Cookie 名称；浏览器 Cookie 不按端口隔离，旧版同名 Secure Cookie 可能阻止 HTTP 页面覆盖，浏览器会报告 `OverwriteSecure`。升级后强制刷新前端并重新登录；仍有冲突时清除对应站点的 Cookie。
5. 如果浏览器禁用了站点 Cookie，允许该站点存储 Cookie，并直接在浏览器标签页打开工作台。页面会明确提示“口令已验证，但浏览器未能保存或发送登录 Cookie”。IP 与域名保存各自的登录状态，切换访问地址后需分别登录。
