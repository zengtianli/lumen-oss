# Lumen CLI / API / MCP

三种接口和Mac界面调用同一个 `lumen.service.dispatch`，使用同一库、版本保护、内容schema和错误。先执行 `lumen schema --json` 获取当前实际能力及字段，不需要读取skill。

## 稳定 RPC

`--params` 接受内联 JSON 或 JSON 文件路径。大门户与完整页面快照使用参数文件，避免命令行长度限制。文件应按私有权限保存，用后清理。

```sh
lumen rpc build --params '{"content":{"title":"成果解释","pages":[{"id":"overview","title":"整体","blocks":[{"id":"purpose","type":"prose","text":"说明用途、结论和依据。"}]}]}}' --json
lumen rpc get --params '{"document_id":"doc-编号"}' --json
```

正文更新通过 `save {document_id,page_id,expected_version,patches,feedback,html?}`。新结构页面的 patches 是JSONPointer及原值、新值；旧页面提供完整HTML快照。批注与修改一并保存。Agent 提交重建通过 `build {content,document_id,expected_version}`，已有人工修改与新内容冲突时返回 `merge_conflict`。

个人属性通过 `metadata` 保存。`get` 的 `document.metadata_revision` 是独立于正文版本的属性修订号，旧文档默认从 0 开始；标题、项目、标签、笔记、收藏、归档或来源路径实际变化后递增，同值重复写入保持不变。构建、正文保存或历史恢复若改变这些全局属性也会递增；只改正文而全局属性未变时保持。读取后编辑笔记、标题或其他属性时，将 `expected_metadata_revision` 一并提交；若同时提交 `expected_version`，两个编号都必须匹配，缺少属性修订号也会拒绝覆盖。冲突回执给当前修订号，重新读取后合并修改。CLI 对应 `--expected-metadata-revision`。完全省略两个 `expected` 字段的即时属性命令保留无条件更新语义。

`get` 指定历史版本时，正文、页面、反馈和正文 `version` 取历史版本；成果标题、个人笔记、分类、收藏等全局属性和 `metadata_revision` 取当前值。历史内容源的标题仍保留在 `content.title`，不会将旧个人属性与当前修订号混在一起。

## 按需本机 API

`lumen serve --port 0` 启动一个显式本机服务，ready回执给实际地址和令牌。客户端用 `Authorization: Bearer <token>`，经 `POST /api/rpc` 发送 `{action,params}`；`GET /api/status`、schema和文档读取按同一鉴权要求。远程监听、任意网页跨源请求和未鉴权写入均拒绝。退出服务不影响已独立运行的生成任务。

## MCP stdio

配置 MCP server 的程序为 `/Applications/Lumen.app/Contents/Resources/lumen`，参数为 `["mcp"]`。工具定义直接给出输入schema；`lumen_build`、`lumen_generate`、`lumen_search`、`lumen_get`、`lumen_save`、`lumen_feedback` 等调用同一dispatch。无需另起HTTP。

## 生成任务

`generate` 固定一个文档、来源快照、解释要求、总截止和有限重试预算。返回 task_id 后用 `task` 读真实状态，`cancel` 取消自己的进程树，`resume` 只接续尚未完成步骤。API和MCP同样先返回任务。任务成功必须有实际HTML入口、链接资源验证和入库读回证据。

`kind`、`timeout_seconds` 和 `max_source_bytes` 省略或设为 JSON `null` 时，分别读取当前 `settings.default_kind` 和 `settings.generation`；显式请求值优先。CLI 的 `--kind`、`--timeout-seconds`、`--max-source-bytes` 遵循同一规则。任务 `params` 保存实际用途和预算，后续修改设置不改变已建任务；同一幂等请求返回原任务，接续仍使用原快照与预算。

生成时间预算为 10 至 3600 秒（请求可用小数，持久设置使用整数），覆盖来源快照与后续生成、构建；停滞预算最多 300 秒，取消和最多三次尝试的合同保持不变。`max_source_bytes` 为 1 至 400000 的整数，限制快照中全部文件的 UTF-8 文本字节总量，包含 Word/PDF/PPT 提取文字；超出时截断并记录范围，过小预算导致无可读文字时拒绝生成。它不表示原文件大小或整个 JSON 回执大小。来源快照另保留最多 150 个文件、每文件最多 24000 文本字节与最多 30 秒的读取边界。

`--state-dir` 可隔离整套库、配置与任务，适合测试；没有它时落当前系统用户的私有目录。配置导出与iCloud同步不包含知识内容或模型凭证。
