[English](README_EN.md)

# Lumen · 知页

把 HTML 成果整理成可阅读、可编辑、可长期保存的本机知识库。原生 Mac 窗口、CLI、按需本机 API 和 MCP 共用一个引擎与版本规则。

可从标准 JSON 构建门户、报告与教学页，导入 HTML，编辑正文、批注与笔记，搜索章节、比较版本、恢复历史和导出离线包。PPTX 转换保留页序、文字、表格和图片；复杂对象需要人工核对。目录图谱通过另外安装的 Folio 提供。

本树是工具的独立源码快照：示例均为虚构内容。不会附带作者的知识库、真实成果、内部台账或部署配置。

## 从源码使用

Python 3.12 或更新版本；Mac 窗口要求 macOS 14 或更新版本及 Xcode Command Line Tools。先在本目录建立环境：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python engine/entry.py --state-dir ./state status --json
.venv/bin/python engine/entry.py --state-dir ./state build --content examples/content.json --json
.venv/bin/python engine/entry.py --state-dir ./state list --json
.venv/bin/python engine/entry.py guide overview --json
```

回执中的 `entry_path` 是生成的 HTML 入口。`--state-dir` 可隔离测试库；日常不指定时使用当前用户的 `~/Library/Application Support/Lumen`。来源文档不会被覆盖。

```sh
bash build.sh
```

得到 `build/Lumen.app`，可在构建目录打开窗口。包内含冻结的 Python 引擎；运行时不需要源仓库。该命令只制作本地 ad-hoc 测试候选，不安装、不公证、不发行。供其他人下载的 Mac 安装包须另走 Developer ID 签名、Apple 公证和装订；本快照尚无公开下载包。

## 模型生成与可选能力

`generate` 调用已安装、已由使用者登录的官方 `codex` 或 `claude` CLI。模型沿各 CLI 自己的配置，不指定固定档位。`LUMEN_PROVIDER=codex` 或 `claude` 选择默认提供方，亦可逐次传 `--provider`；找不到 CLI 或未登录时明确失败。工具不会替用户登录，不读取作者的账号、令牌或路由配置。

```sh
.venv/bin/python engine/entry.py agent-status --json
.venv/bin/python engine/entry.py --state-dir ./state generate --source examples/source.md --request '解释这个虚构项目的用途和步骤' --provider codex --json
```

模型请求会将你选择的来源文字发送给所选提供方。离线 `build`、导入、阅读与保存无需模型。自定义兼容适配器可通过 `LUMEN_LLM_CLIENT_DIR` 指定，须提供 `llm_client.provider_settings()` 与 `chat(...)`。

发现功能默认只读 `~/Documents/Lumen`，也可用 `discover --roots <目录>`；不自动导入或扫描全电脑。API 默认不常驻，仅显式 `serve` 时启动并要求令牌；`mcp` 使用 stdio。配置导入导出与可选 iCloud 只传可迁移设置，知识内容、来源路径和模型凭证留在本机。

更新检查使用本仓的 [GitHub Releases](https://github.com/zengtianli/lumen-oss/releases)。正式安装包仍待签名、公证和装订，当前尚无公开 Release，更新检查可能返回“无发行版”。

## 验证与限制

```sh
.venv/bin/python scripts/parallel-tests.py --jobs 12
```

测试使用隔离目录和虚构夹具，不调用真实模型。Folio 未安装时可选图谱集成测试跳过；其他核心测试仍执行。模型调用参数、超时与错误处理有本地替身测试；真实在线生成、窗口人工操作及发行等级不由源码构建通过来证明。扫描 PDF 需先 OCR，Word 仅读取主文档段落与表格；外部专业绘图、深度审阅、朗读及部署程序不包含在本快照中。

原创代码采用 [MIT](LICENSE)。第三方依赖说明见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
