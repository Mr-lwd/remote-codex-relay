# 维护与发布

项目面向 Linux / WSL2 的个人及受信任团队。源码遵循 MIT 许可证；Codex CLI、账号和模型服务由使用者自行准备。兼容性、使用边界与实际测试范围以 README、开发文档及 CI 结果为准。

## 提交前的隐私检查

在仓库根目录执行：

```bash
python3 scripts/release.py --check --git-check --history
git diff --check
git diff
git diff --cached
```

检查器同时校验当前可发布文件、Git 索引及所有本地引用可达的提交。它拒绝发布范围之外的已跟踪文件、符号链接、常见密钥、非示例 IPv4 和个人主目录路径；还检查提交与标签消息。浅克隆需先运行 `git fetch --unshallow`。尚未获取的远程引用、不可达对象和图片里的文字不属于自动历史扫描范围，截图必须另行人工审阅。

需要检测自己的域名、邮箱或其他特殊标识时，把每个完整值单独写一行，保存在被忽略的 `privacy.local.txt` 中，然后执行：

```bash
python3 scripts/release.py --check --git-check --history \
  --private-values-file privacy.local.txt
```

这个本地文件不要提交；检查器不会回显匹配内容。通用规则无法识别所有秘密，仍需人工审查差异。`.gitignore` 不会移除已跟踪或历史中的文件；检查失败时先处理相关提交，再公开仓库。不要未经协作者协调改写共享历史。

Git 提交会公开作者、提交者的姓名和邮箱，它们独立于源码隐私检查。发布前用 `git log --all --format=fuller` 本地检查，并在托管平台选择自己的 noreply 邮箱；新身份配置不会改变旧提交。`.git/config` 中的远程地址不会进入源码包，分享仓库时仍不要复制含凭据的远程 URL。

## 准备一个版本

1. 在独立开发目录运行[开发文档](development.md)中的格式、Python、构建、浏览器及迁移检查。
2. 同步 `package.json`、`package-lock.json` 的两处版本和 `server/__init__.py`，在 CHANGELOG 下添加同名版本节。发布脚本会检查一致性。
3. 审阅并暂存改动后，再执行上述隐私检查，检查索引中的实际内容。
4. 执行 `python3 scripts/release.py`，生成源码 ZIP 和 `SHA256SUMS`。
5. 在另一个目录解压 ZIP，按 README 启动。也可运行 `python3 tests/manager_smoke.py --archive output/releases/codex-relay-版本.zip`，自动验证该发布包的独立安装、目录迁移、登录与附件保留。

发布包只含白名单内的源码、文档、锁文件和部署模板，不含账号、本地配置、口令、依赖、前端生成物、备份或 Git 历史。用户启动时会安装依赖并构建前端，因此首次启动需要能访问包源。

## GitHub 配置与发布

仓库内提供问题反馈、功能建议和 PR 模板，Dependabot 定期提出 npm、pip 和 Actions 更新。合并依赖升级前运行兼容性检查，不自动合并。

在 GitHub 仓库设置中启用私有漏洞报告，并为主分支配置保护规则：要求 PR 审阅和 `verify` 的 Python 3.11 / 3.12 检查通过。设置依赖托管平台，单靠提交源码不能自动启用。

普通 push / PR 执行 CI。手动运行 `checks` 工作流，或推送与版本匹配的 `v版本号` 标签时，只有验证矩阵通过后才生成 `codex-relay-source` 构建产物，包含源码 ZIP 和校验和。工作流不自动创建公开 Release。

确认提交和 CI 后，为发布提交创建标签，再将产物附到 GitHub Release，说明新功能、迁移方法和已知边界。不要把整个部署目录压缩上传，也不要使用包含私有旧提交的 Git bundle。

新版本中的 CLI 实验协议可能受上游变更影响；升级后用自己的测试账号验证原生协议与一次实际任务。出现问题时保留配置和运行数据，使用已保存的上一版源码；不要直接删除数据库来回退。
