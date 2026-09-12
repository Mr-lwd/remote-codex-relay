# 开发、测试和发布

## 开发环境

从干净 checkout 开始运行 `bash scripts/setup.sh --dev`。启动 `.venv/bin/python -m server`，另一终端运行 `npm run dev`。开发代理默认连接后端 8000 端口，前端构建由 FastAPI 同源托管。不要对管理活跃任务的服务启用热重载。

`setup.sh` 现在复用标准库管理器的安装流程，只准备环境；已有服务运行时请使用 `repair`，或先停止服务。管理器重建虚拟环境时保留已检测到的运行/开发/浏览器依赖类型。

使用独立 `relay.toml`，把 `data_dir`、`workspace_root`、`default_cwd` 和 `codex.home` 指向自己的测试环境。单元测试和浏览器 smoke 自行创建临时数据，不依赖本机已有账号、会话、附件或调试浏览器。

## 自动化检查

```bash
npm run format:check
.venv/bin/ruff check server scripts tests
.venv/bin/ruff format --check server scripts tests
.venv/bin/python -m pytest
npm run build
```

需要格式化时使用 `npm run format` 和 `.venv/bin/ruff format server scripts tests`。测试会覆盖配置解析、首次登录、数据库幂等升级、会话历史、命令/模型传递、目标中断与队列、显式审批、目录边界、文件类型/大小/归属/持久化。不会调用模型或改变真实会话。`tests/conftest.py` 在导入应用前覆盖所有 Relay 和 Codex 数据路径；共享 fixture 集中于 `tests/relay_fixtures.py`。

## 可复现浏览器验收

```bash
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python tests/browser_smoke.py
```

脚本创建独立会话 fixture、随机本地端口和临时运行目录，启动自己的服务，验证完成或失败后关闭服务。测试使用已构建的 `dist/`；`--dist 路径` 可指定其他构建。结果和截图保存到 `runtime/browser-smoke/`，用 `--output 路径` 可修改。

覆盖全新安装登录、旧版 Secure Cookie 冲突、刷新与退出、Cookie 被拦截时的提示、默认模型、真实目录边界、10 条/全部历史、图片/PDF 上传下载、表格公式、模型强度、输入区附件位置，以及五种桌面/平板/手机视口。支持 `--cdp http://127.0.0.1:9222` 连接已有浏览器，仅创建并关闭自己的上下文，不关闭共享浏览器。

队列验收使用合成 API/SSE 事件，覆盖执行中输入后发送、逐条删除、失败重试、跨设备更新、指令/附件及小屏底部按钮可见性。`tests/test_message_queue.py` 验证真实后端排队、删除、顺序执行、配置传递和取消与启动的竞争。可用 `tests/browser_queue.py --url 地址 --output runtime/queue-check` 单独检查已部署的前端；它拦截测试页面的 API 请求，不提交真实任务。

会话隔离验收覆盖反复切换、目标栏残留、独立草稿/附件、延迟发送/删除/停止/审批、过期推送和历史请求，以及新任务完成时保留手动选择的会话。桌面和手机场景已纳入 smoke，也可用 `tests/browser_sessions.py --url 地址 --output runtime/session-check` 单独检查；所有 API 操作均由合成数据接管。

输入性能验收使用含表格和公式的 10 条近期记录及 60 条完整历史，测量实际键盘输入延迟，并检查输入或相同推送不会重建历史 DOM、更新后的回复和附件仍可显示。已纳入 smoke，也可运行 `tests/browser_typing.py --url 地址 --output runtime/typing-check`；`--baseline` 记录对比数据而不强制性能断言。

这套测试使用合成历史，不执行模型；它不能证明你的账号支持目标协议或所选模型，也不替代手机真机验证。

## 安装与目录迁移验收

`tests/test_manager.py` 使用假安装器和临时目录验证下载/构建失败恢复、旧路径冲突、同名服务保护、配置重写、端口占用、并发管理和任务保护；不会调用真实用户服务。`tests/test_maintenance.py` 验证维护期间的写请求拦截和中文 503 页面。

需要验证真实依赖安装与迁移时运行：

```bash
.venv/bin/python tests/manager_smoke.py
```

该验收需要可用的包网络或缓存、系统 venv 和 Node 22/24。它复制源码到临时中文/空格目录，安装依赖并以前台模式启动，在其他工作目录重复调用命令，再移动项目、删除构建入口并执行修复，验证原口令、合成会话和已上传附件。使用独立配置和进程，不接管真实 systemd 服务或读取 Codex 账号。报告与日志保存在 `runtime/manager-smoke/`。

## 原生 Codex 接入检查

```bash
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/check_codex.py
```

第一步校验依赖、CLI 子命令、已有数据库 schema 和前端产物；第二步导出实验协议 schema，检查目标/会话/回合所需方法，再进行原生 initialize 和 model/list。不会创建会话或发起推理。原生检查需要已配置的 Codex，可产生 CLI 自己的常规日志。完整执行验收仍需用你自己的测试会话发送一个明确的小任务，并确认回复、审批和目标停止行为。

## Nginx 模板验证

`nginx -t` 会初始化缓存目录，可能更改目录属主。使用独立 `-c` 配置验证模板时，必须显式把 `client_body_temp_path`、`proxy_temp_path`、`fastcgi_temp_path`、`uwsgi_temp_path` 和 `scgi_temp_path` 指向测试目录；仅设置 `-p` 不能隔离编译时指定的绝对缓存路径。线上配置通过系统的 `nginx -t` 检查，确保缓存目录属主与实际 worker 用户一致。

## 依赖维护

- `package-lock.json` 固定前端完整依赖图并使用公共 npm registry URL；安装用 `npm ci`。
- `requirements.in` 记录 Python 直接依赖，`requirements.txt` 固定运行及文档工具的完整解析结果。
- `requirements-dev.txt` 添加 pytest/Ruff，`requirements-browser.txt` 添加 Playwright 与其依赖；各文件包含前一层，不需要全局 pip 安装。
- 当前 Python 锁基于 Linux / Python 3.12。CI 配置验证 3.11 与 3.12；不能将未运行的 CI 当作其他平台已通过的证据。

更新 Python 依赖时在临时 venv 中安装 `requirements.in`，用 `python -m pip freeze` 审查版本差异，更新对应锁文件，再从全新 venv 安装并跑完整检查。浏览器库升级后重新安装 Chromium。不要直接导出维护者的全局环境，也不要把包索引账号写入锁文件。CLI 不是 Python/npm 项目依赖，升级 CLI 要单独验证实验协议与本地存储兼容性。

## 源码发布

```bash
python3 scripts/release.py --check --git-check --history
python3 scripts/release.py
```

输出 `output/releases/codex-relay-版本.zip` 和 `SHA256SUMS`。包只含脚本白名单中的源码、测试、文档、依赖锁与部署模板，不含 `dist/`、运行数据、依赖、凭据或 Git 历史。打包使用固定时间戳，同一内容生成同一摘要；拒绝符号链接、已知密钥特征、非示例 IPv4 地址和个人主目录路径，也检查 Python 字符串常量拼接。网络示例使用本机地址或 RFC 5737 文档地址，路径使用 `~` 或占位符；测试数据使用合成值。新增发布文件时维护脚本白名单，检查解压后的内容再发布。

`.gitignore` 默认忽略未列入发布范围的文件，仅放行源码、必要脚本、配置模板、文档和 CI 测试。新增目录或资源类型时同步更新 `.gitignore` 与发布脚本；本地探针、截图、日志和验收报告放在 `runtime/` 或 `output/`。

发布前应在另一个目录解压该包，按 README 执行安装、诊断和启动，确认没有使用原项目 `.venv`、`node_modules`、`dist` 或运行数据。此项目不包含自动推送或创建远程仓库的脚本。

可用 `python3 tests/manager_smoke.py --archive output/releases/codex-relay-版本.zip` 直接验收生成的源码包。版本一致性、Git 历史/自定义隐私检查和 GitHub 设置见[维护与发布](releasing.md)。
