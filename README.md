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

推荐 **Linux / WSL2**。准备 Python **3.11+**、Node.js **22 / 24**、npm，以及已登录或配置好模型服务的 Codex CLI。当前已验证的 CLI 版本为 **0.154.0**。

在项目根目录安装依赖并构建前端：

```bash
bash scripts/setup.sh
```

脚本会创建虚拟环境并生成 `relay.toml`，保留已有配置。启动前核对工作目录和模型 ID：默认提供 `gpt-5.6-sol`、`gpt-6-astra`，可按你的模型服务修改。完整选项见[配置参考](docs/configuration.md)。

**API 快速切换推荐：** [CC Switch CLI](https://github.com/SaladDay/cc-switch-cli) 支持在终端集中管理和切换 Codex、Claude Code 等工具的 API 服务商配置，适合需要在多个 API 接口之间快速切换的用户。

检查环境并启动服务：

```bash
.venv/bin/python scripts/doctor.py
.venv/bin/python -m server
```

打开 **http://127.0.0.1:8000**，在另一个终端读取首次启动生成的登录口令：

```bash
cat runtime/access.txt
```

登录后选择已有会话，或点击「新建任务」浏览工作目录并开始。自定义数据目录时，从该目录的 `access.txt` 读取口令。远程访问与常驻运行见[部署指南](docs/deployment.md)。

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

## License

[MIT](LICENSE)。
