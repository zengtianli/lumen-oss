"""Dependency-free MCP stdio transport for the same Lumen RPC actions."""
from __future__ import annotations

import json
import sys


def tool_specs():
    from .settings import GENERATION_LIMITS
    string = {"type": "string"}
    integer = {"type": "integer"}
    boolean = {"type": "boolean"}
    common = {"document_id": string, "expected_version": integer}
    actions = {
        "status": ("读取本机知识库、生成任务、版本和设置状态。", {}, []),
        "guide": ("读取Lumen内置标准工作流，agent无需读取任何skill。无topic时列出主题；按主题取正文再通过同源接口执行。", {"topic": string}, []),
        "discover": ("复用现有成果索引，在指定根目录发现重要HTML入口。只读候选，不自动导入、生成或发布；默认排除测试SVG。", {"roots": {"type": "array", "items": string}, "limit": integer}, []),
        "list": ("查找成果，按项目、类型、标签、收藏或归档筛选。", {"query": string, "project": string, "kind": string, "tag": string, "favorite": boolean, "archived": boolean}, []),
        "get": ("读取成果全文结构、章节路径、版本、批注和笔记；更新前先读取。不提供version读取当前版。", {"document_id": string, "version": integer}, ["document_id"]),
        "build": ("将已经整理的标准内容生成可编辑 HTML 并入库，不调用模型。content={title,home,pages:[{id,title,summary,blocks:[{id,type:'prose',text}]}]}。portal逐层解释；report可单页；teach用于教学。更新旧文档必须 document_id+expected_version；冲突拒绝覆盖。", {**common, "content": {"type": "object"}, "kind": {"enum": ["portal", "report", "teach"]}, "project": string, "tags": {"type": "array", "items": string}, "sources": {"type": "array"}, "idempotency_key": string}, ["content"]),
        "import": ("导入本机 HTML 或 portal 入口，收齐依赖并提供编辑和版本。默认排除测试SVG。", {"path": string, "kind": string, "project": string, "tags": {"type": "array", "items": string}, "title": string, "include_test": boolean}, ["path"]),
        "convert": ("调用现有底座将目录知识图谱或PPT转换成可编辑HTML并入库；graph输入目录、ppt输入PPTX。", {"source": string, "kind": {"enum": ["graph", "ppt"]}, "project": string, "tags": {"type": "array", "items": string}}, ["source", "kind"]),
        "save": ("保存页面修改或批注为新版本。结构内容使用 JSONPointer patches[{path,value,original}]；静态页使用完整html。先get读当前version，expected_version防止覆盖。", {**common, "page_id": string, "patches": {"type": "array", "items": {"type": "object"}}, "feedback": {"type": "object"}, "html": string}, ["document_id", "page_id", "expected_version"]),
        "metadata": ("更新标题、项目、标签、收藏、归档、笔记。先get读取document.metadata_revision，用expected_metadata_revision防止旧客户端覆盖笔记；提供expected_version时必须同时提供属性修订号。不提供两个expected字段时为即时无条件更新。归档保留所有内容。", {**common, "expected_metadata_revision": {"type": "integer", "minimum": 0}, "title": string, "project": string, "tags": {"type": "array", "items": string}, "favorite": boolean, "archived": boolean, "notes": string}, ["document_id"]),
        "versions": ("读取不可覆盖的历史版本。", {"document_id": string}, ["document_id"]),
        "restore": ("将旧版本恢复为新版本，历史仍保留。", {**common, "version": integer}, ["document_id", "version", "expected_version"]),
        "compare": ("比较两个版本的结构内容与批注变化。", {"document_id": string, "from_version": integer, "to_version": integer}, ["document_id", "from_version"]),
        "export": ("导出整套可独立离线打开的HTML包。", {"document_id": string, "output": string, "version": integer}, ["document_id", "output"]),
        "search": ("全文搜索知识，结果定位到具体章节、段落和本地路径。", {"query": string, "limit": integer}, ["query"]),
        "feedback": ("读取批注、用户意见和个人笔记供agent继续更新。", {"document_id": string, "page_id": string}, ["document_id"]),
        "position": ("读取或更新阅读章节、滚动位置和锚点。", {"document_id": string, "page_id": string, "scroll_y": {"type": "number"}, "anchor": string}, ["document_id"]),
        "settings": ("读取或更新统一生成标准和界面词表，App/CLI/API同源。", {"values": {"type": "object"}}, []),
        "config-export": ("导出可迁移设置，不导出密钥和机器运行状态。", {"output": string}, ["output"]),
        "config-import": ("从JSON文件导入可迁移配置。", {"source": string}, ["source"]),
        "reindex": ("从实际成果重建派生全文搜索索引。", {}, []),
        "generate": ("独立来源生成，快回任务ID，后台运行。已有结构内容请build避免二次模型调用。kind、timeout_seconds、max_source_bytes省略或null时取当前Lumen设置；显式值覆盖默认并固定进任务。来源快照文本按UTF-8字节严格截断，后续接续使用同一快照和预算。模型按用户官方CLI配置；source本机路径、request解释要求。更新旧文档需要版本。", {**common, "source": string, "request": string, "kind": {"enum": ["portal", "report", "teach", None]}, "project": string, "tags": {"type": "array", "items": string}, "provider": {"enum": ["codex", "claude", None]}, "timeout_seconds": {"type": ["number", "null"], "minimum": GENERATION_LIMITS["timeout_seconds"][0], "maximum": GENERATION_LIMITS["timeout_seconds"][1]}, "max_source_bytes": {"type": ["integer", "null"], "minimum": GENERATION_LIMITS["max_source_bytes"][0], "maximum": GENERATION_LIMITS["max_source_bytes"][1]}, "idempotency_key": string}, ["source", "request"]),
        "tasks": ("读取生成任务真实状态。", {}, []),
        "task": ("用task_id读取任务进度、错误、checkpoint和成果完成证据。", {"task_id": string}, ["task_id"]),
        "cancel": ("取消此生成任务，收束它的模型子进程，不干扰其他任务。", {"task_id": string}, ["task_id"]),
        "resume": ("在有限预算内接续同一个任务，仅继续未完成步骤；已保存draft不会重新调用模型。", {"task_id": string}, ["task_id"]),
    }
    from .agent import content_schema
    actions["build"][1]["content"] = content_schema()
    return [{"name": "lumen_" + action, "description": description,
             "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}
            for action, (description, properties, required) in actions.items()]


def handle_message(message, state_dir=None, dispatcher=None):
    from .service import dispatch
    dispatcher = dispatcher or dispatch
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}}
    method, params = message["method"], message.get("params") or {}
    if "id" not in message:
        return None
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Parameters must be an object"}}
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {"protocolVersion": requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18") else "2024-11-05",
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "Lumen", "version": "1.0.0"},
                  "instructions": "个人本机HTML知识库。已有解释内容调用lumen_build；先lumen_get再基于expected_version更新，用户修改冲突不可覆盖。"}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_specs()}
    elif method == "tools/call":
        name = params.get("name")
        specifications = {tool["name"]: tool for tool in tool_specs()}
        if name not in specifications:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown tool"}}
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Arguments must be an object"}}
        schema = specifications[name]["inputSchema"]
        missing = [field for field in schema["required"] if field not in arguments]
        unknown = sorted(set(arguments) - set(schema["properties"]))
        if missing or unknown:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Missing or unknown arguments", "data": {"missing": missing, "unknown": unknown}}}
        envelope = dispatcher(name[6:], arguments, state_dir)
        result = {"content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False)}], "isError": not envelope["ok"], "structuredContent": envelope}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def run_stdio(state_dir=None, input_stream=None, output_stream=None):
    input_stream, output_stream = input_stream or sys.stdin, output_stream or sys.stdout
    for line in input_stream:
        try:
            if len(line) > 8_000_000:
                raise ValueError("request too large")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = handle_message(request, state_dir)
        except (ValueError, TypeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32603, "message": "Internal error"}}
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
    return 0
