# Lumen 内容与运行契约

原生 AppKit/WKWebView 窗口与 Python 引擎使用同一 JSON dispatcher。运行时不读取外部技能或项目指导。

默认本机单用户，无产品账号。知识库位于当前用户 Application Support/Lumen；测试显式使用隔离 state-dir。来源、正文、编辑快照、批注、版本与属性按成果保存。

标准内容支持 portal、report、teach。更新必须提供当前版本；冲突不覆盖。HTML 导入复制必要关联资源，缺失和越界依赖明确失败。原件不改写。

CLI/API/MCP 契约见 [API.md](API.md) 与 `schema --json`。GUI 无常驻 HTTP。可选模型走使用者自己安装、登录的官方 CLI，图谱走另外安装的 Folio。

公开源码构建只出本地测试候选；发行签名、公证、安装、登记与公开动作沿独立流程处理。
