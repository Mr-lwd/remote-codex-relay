**[简体中文](README.md)** | [English](README.en.md)

# Codex Relay — 跨设备的 Codex 网页工作台

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/readme-banner-dark.svg">
    <img src="docs/images/readme-banner-light.svg" width="880" alt="Codex Relay — Your Codex sessions. Any screen.">
  </picture>
</p>

<p align="center">
  <img src="docs/images/readme-badges.svg" width="354" alt="MIT License · Python 3.11+ · Node.js 22 / 24">
</p>

**把本机 Codex 带到浏览器，在电脑、平板和手机上继续同一项工作。**

Codex Relay 是一个自托管网页工作台，通过本机 Codex CLI 管理会话、执行任务、处理审批和收发文件。使用你自己的 Codex 账号、模型服务与技能配置，任务由服务器上的服务托管。

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="docs/usage.md">使用说明</a> ·
  <a href="docs/configuration.md">配置参考</a> ·
  <a href="docs/deployment.md">部署指南</a> ·
  <a href="docs/deployment.md#常见问题">常见问题</a>
</p>

<p align="center">
  <a href="docs/images/interface-overview.png">
    <img src="docs/images/interface-overview.png" width="1120" alt="桌面对话与文件下载、绘画参考、移动端目标控制三视图">
  </a>
  <br>
  <sub>实际界面，使用演示数据。点击图片查看大图。</sub>
</p>

## 快速开始

推荐 **Linux / WSL2**。准备 Python **3.11+**（含 venv）、Node.js **22 / 24**、npm，以及已登录或配置好模型服务的 Codex CLI。当前已验证的 CLI 版本为 **0.154.0**。首次安装需能访问 PyPI 和 npm 包源。

从本仓库的 **Code → Download ZIP** 下载并解压，或使用 Code 中的地址 `git clone`；进入解压或克隆后的项目根目录（应能看到 `scripts/` 和 `relay.example.toml`），执行：

```bash
bash scripts/relay.sh start
```

管理器会检查 Python、Node 和 Codex，按锁文件补齐项目依赖、构建前端，并生成用户 systemd 服务；没有可用的用户 systemd 时以前台方式运行。已有配置和数据会保留，缺失的 `relay.toml` 会从模板生成。系统依赖不满足时会显示安装提示，不自动执行 sudo。

默认提供 `gpt-5.6-sol`、`gpt-6-astra`，需由你配置的模型服务支持；不支持时修改 `[ui]` 的 `models` 和 `default_model`。启动前也可以先复制 `relay.example.toml` 为 `relay.toml`，设置工作目录、模型和监听地址；已有文件直接编辑，完整选项见[配置参考](docs/configuration.md)。

**API 快速切换推荐：** [CC Switch CLI](https://github.com/SaladDay/cc-switch-cli) 支持在终端集中管理和切换 Codex、Claude Code 等工具的 API 服务商配置，适合需要在多个 API 接口之间快速切换的用户。

查看运行状态，或选择前台运行：

```bash
bash scripts/relay.sh status
# 没有后台服务运行时：
bash scripts/relay.sh start --foreground
```

打开 **http://127.0.0.1:8000** ，在另一个终端读取首次启动生成的登录口令：

```bash
cat runtime/access.txt
```

登录后选择已有会话，或点击「新建任务」浏览工作目录并开始。自定义数据目录时，从该目录的 `access.txt` 读取口令。远程访问与常驻运行见[部署指南](docs/deployment.md)。

### 日常管理与目录迁移

| 命令 | 用途 |
| --- | --- |
| `bash scripts/relay.sh start` | 检查并启动；配置和依赖未变化时重复执行不会重启 |
| `bash scripts/relay.sh repair` | 自动修复旧路径、虚拟环境或前端构建，并恢复运行 |
| `bash scripts/relay.sh stop` | 停止当前项目的服务 |
| `bash scripts/relay.sh restart` | 检查后重启服务 |
| `bash scripts/relay.sh status` | 查看进程、路径、依赖、前端和访问地址 |
| `bash scripts/relay.sh logs` | 查看安装日志并持续跟踪服务日志，Ctrl+C 退出查看 |

项目改名或移动后，在**新目录**执行 `bash scripts/relay.sh repair`。支持中文和空格路径；也可从其他目录通过脚本的完整路径调用。移动前先等待任务结束、处理队列并执行 `stop`，整体带走 `relay.toml`、`runtime/` 和自定义的运行数据。管理器发现执行任务或待处理队列时会停止维护操作，不强制中断任务。

修复会备份被替换的配置和生成文件，重新创建失效的虚拟环境；安装或构建失败会恢复旧文件。旧路径信息冲突、原目录仍存在或同名服务属于其他项目时会明确报错。Codex 原生会话内的工作目录和外部文件路径不自动改写，边界与备份位置见[迁移说明](docs/deployment.md#备份和迁移)。

开机自启需显式运行 `bash scripts/relay.sh start --enable`。只安装而不启动仍可使用 `bash scripts/setup.sh`；开发依赖使用 `bash scripts/setup.sh --dev`。

### 通过公网 IP 或域名访问

默认的 `127.0.0.1` 只允许服务器本机访问。需要从其他设备直接连接时，修改本地 `relay.toml` 中已有的 `[server]` 配置（不要重复添加该节）：

```toml
[server]
host = "0.0.0.0"
port = 8000
secure_cookie = false # 直接通过 HTTP 访问时使用 false。
```

修改后执行 `bash scripts/relay.sh restart`，管理器会先检查执行任务与队列。已安装依赖时，也可手动启动一次临时测试：

```bash
.venv/bin/python -m server --host 0.0.0.0 --port 8000
```

`0.0.0.0` 表示监听所有 IPv4 网卡，浏览器应打开 `http://服务器公网IP:8000/` 或 `http://你的域名:8000/`。域名的 A 记录须指向服务器公网 IP；云平台安全组和主机防火墙须允许 TCP 8000 入站。云主机通常通过内网网卡接收公网转发，无需把公网 IP 填入 `host`。

HTTP 直连可用于连通性测试；长期公网访问请配置 [HTTPS 反向代理](docs/deployment.md#nginx-和-https)，将后端改回 `host = "127.0.0.1"` 并设置 `secure_cookie = true`。HTTPS 代理配置与 HTTP 直连配置不能混用，否则浏览器可能无法保存登录状态。

如果输入口令后仍停在登录页，先核对 `secure_cookie` 与访问协议，再检查页面的 Cookie 提示，详见[登录排查](docs/deployment.md#登录排查)。登录口令是 `runtime/access.txt` 的内容，不是模型服务的 API Token。`relay.toml` 是被 Git 忽略的本地配置，提交部署说明时修改 README 和 `relay.example.toml` 即可。

## 核心能力

- **多端会话** — 电脑、平板和手机共用工作台；支持真实目录浏览、会话创建、重命名与归档。
- **持续目标** — 通过 `/goal` 持续推进任务，在输入框上方编辑、暂停、继续或清除目标。
- **模型与权限** — 按会话选择模型和推理强度，在聊天中处理审批与提问，执行期间可停止回复。
- **文件与绘画** — 上传和返回图片、PDF、Office 等常用文件；用多色画板绘制草图并作为图片参考。
- **清晰的对话** — 支持 Markdown 表格、数学公式与独立指令块；默认加载最近 10 条记录，上滑查看完整历史。
- **实时执行状态** — 消息与任务动态自动同步，执行现场按需打开，后续消息可排队执行。

## 常用指令

普通消息默认直接执行，也可以从输入框的 `/` 菜单选择指令。

| 指令 | 用途 |
| --- | --- |
| `/goal 目标内容` | 设置持续目标，后续通过目标栏控制 |
| `/plan 需求` | 进入计划模式，先规划任务 |
| `/default` | 返回执行模式 |
| `/compact` | 压缩原生上下文，保留网页聊天历史 |
| `/model`、`/permissions` | 打开模型或权限选择器 |
| `/status`、`/help` | 查看执行现场或全部指令 |

`/goal` 与 `/plan` 分别用于持续目标和计划模式。只有完整指令名会触发命令；`/tmp/report.pdf`、`/goal/report.md` 等路径可作为普通文本发送。详见[使用说明](docs/usage.md)。

## 工作方式与部署

浏览器中的 React 界面通过 HTTP / SSE 连接 FastAPI 服务，由服务调用本机 Codex CLI。执行任务、使用工具和访问模型服务的能力来自你的 Codex 配置。关闭浏览器后，常驻服务仍可继续托管任务。

工作目录、模型和监听地址统一配置在本地 `relay.toml`（[配置模板](relay.example.toml)），环境变量可覆盖配置文件。默认仅监听本机；对外访问使用 HTTPS，systemd 和 Nginx 示例见[部署指南](docs/deployment.md)。图片上传、预览和绘画无需生图 API；生成新图片需自行配置 Codex 的相应技能或工具。

实例共用服务器用户的会话和文件权限，适合个人或受信任团队；服务以单进程运行。账号凭据、本地配置与运行数据不属于源码，详细使用边界见[安全说明](SECURITY.md)。

## 文档导航

| 你想做什么 | 从这里开始 |
| --- | --- |
| 管理会话、目标、审批与附件 | [使用说明](docs/usage.md) |
| 更换模型、工作目录和监听配置 | [配置参考](docs/configuration.md) · [配置模板](relay.example.toml) |
| 配置 HTTPS、常驻服务或迁移数据 | [部署指南与常见问题](docs/deployment.md) |
| 理解项目结构与 Codex 接入方式 | [架构说明](docs/architecture.md) |
| 本地开发、运行测试与维护项目 | [开发文档](docs/development.md) · [贡献指南](CONTRIBUTING.md) |
| 了解使用边界与版本变化 | [安全说明](SECURITY.md) · [变更记录](CHANGELOG.md) |
| 审查隐私、准备源码包与发布版本 | [维护与发布](docs/releasing.md) |

当前源码版本为 **1.1.0**。安装与浏览器自动验收使用独立演示数据，不包含维护者的账号、服务器配置、会话和附件。Codex CLI 使用实验协议，升级 CLI 或更换服务商后应执行[接入检查](docs/development.md#原生-codex-接入检查)。本项目由社区维护，与 OpenAI 无官方隶属关系。

## License

[MIT](LICENSE)。
