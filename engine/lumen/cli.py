"""Lumen's stable JSON RPC and human CLI share service.dispatch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .types import LumenError


def _json_value(value):
    if value is None:
        return None
    if value.lstrip().startswith(("{", "[")):
        return json.loads(value)
    return json.loads(Path(value).expanduser().read_text(encoding="utf-8"))


def _common(parser, *, sub=False):
    default = argparse.SUPPRESS if sub else None
    parser.add_argument("--state-dir", default=default, help="隔离知识库目录；默认用户 Application Support/Lumen")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if sub else False, help="固定 JSON envelope")


def parser():
    result = argparse.ArgumentParser(prog="lumen", description="Lumen · 知页：生成、阅读、编辑和管理本机 HTML 知识库。CLI、App 和 API 共享业务与保存规则。", epilog="退出码：0 成功；1 操作未完成；2 参数错误。")
    _common(result)
    commands = result.add_subparsers(dest="command", required=True)
    def add(name, help):
        item = commands.add_parser(name, help=help)
        _common(item, sub=True)
        return item
    rpc = add("rpc", "稳定接口：ACTION + --params JSON；App 使用同一入口")
    rpc.add_argument("action")
    rpc.add_argument("--params", default="{}", help="JSON 对象或 JSON 文件路径；大页面使用0600参数文件")
    add("status", "版本、知识库与任务状态")
    add("schema", "标准内容与全部接口 schema；使用无需 skill")
    add("agent-status", "官方 CLI 接入状态，不调用模型")
    guide = add("guide", "读取应用内标准工作流，agent无需读取skills")
    guide.add_argument("topic", nargs="?", help="不指定则列出全部主题")
    discover = add("discover", "通过已有成果索引发现指定目录的重要HTML入口，只读候选")
    discover.add_argument("--roots", nargs="+")
    discover.add_argument("--limit", type=int, default=100)
    listing = add("list", "列出和筛选知识成果")
    for flag in ("query", "project", "kind", "tag"):
        listing.add_argument("--" + flag)
    listing.add_argument("--favorite", action="store_true", default=None)
    listing.add_argument("--archived", action="store_true", default=None)
    get = add("get", "读取成果、章节、内容源和意见")
    get.add_argument("document_id")
    get.add_argument("--version", type=int, help="读取指定历史版本")
    build = add("build", "将已有标准 JSON 构建入库，不二次调用模型")
    build.add_argument("--content", required=True, help="JSON 文件或 JSON 对象")
    build.add_argument("--kind", choices=["portal", "report", "teach"], default="portal")
    imp = add("import", "导入已有 HTML 或 portal，复制依赖并提供编辑")
    imp.add_argument("path")
    imp.add_argument("--title")
    imp.add_argument("--kind", choices=["portal", "report", "teach"])
    imp.add_argument("--include-test", action="store_true")
    convert = add("convert", "用现有转换底座将目录图谱或 PPT 建成可编辑 HTML")
    convert.add_argument("--kind", required=True, choices=["graph", "ppt"])
    convert.add_argument("--source", required=True)
    convert.add_argument("--project")
    convert.add_argument("--tags", nargs="+")
    for item in (build, imp):
        item.add_argument("--project")
        item.add_argument("--tags", nargs="+")
    build.add_argument("--document-id")
    build.add_argument("--expected-version", type=int)
    build.add_argument("--idempotency-key")
    generate = add("generate", "从来源和解释要求生成：快回任务 ID，关闭 App 后继续")
    generate.add_argument("--source", required=True)
    generate.add_argument("--request", required=True)
    generate.add_argument("--kind", choices=["portal", "report", "teach"], help="不指定则使用当前默认生成用途")
    generate.add_argument("--project")
    generate.add_argument("--tags", nargs="+")
    generate.add_argument("--document-id")
    generate.add_argument("--expected-version", type=int)
    generate.add_argument("--provider", choices=["codex", "claude"], help="官方 CLI 首选；不指定则继承共享配置")
    generate.add_argument("--timeout-seconds", type=float, help="10至3600秒；不指定则使用当前生成设置")
    generate.add_argument("--max-source-bytes", type=int, help="1至400000字节；不指定则使用当前来源文本预算")
    generate.add_argument("--idempotency-key")
    generate.add_argument("--wait", action="store_true", help="等待此有限任务；默认立即返回")
    save = add("save", "保存内容补丁、批注或旧 HTML 快照为新版本")
    save.add_argument("document_id")
    save.add_argument("page_id")
    save.add_argument("--expected-version", required=True, type=int)
    save.add_argument("--patches", help="JSON 文件或数组")
    save.add_argument("--feedback", help="JSON 文件或对象")
    save.add_argument("--html", help="完整 HTML 快照文件")
    meta = add("metadata", "修改分类、标签、收藏、归档和笔记")
    meta.add_argument("document_id")
    meta.add_argument("--expected-version", type=int)
    meta.add_argument("--expected-metadata-revision", type=int, help="get返回的属性修订号；配合expected-version防止覆盖旧笔记")
    for flag in ("title", "project", "notes"):
        meta.add_argument("--" + flag)
    meta.add_argument("--tags", nargs="*")
    for flag in ("favorite", "archived"):
        group = meta.add_mutually_exclusive_group()
        group.add_argument("--" + flag, dest=flag, action="store_true", default=None)
        group.add_argument("--no-" + flag, dest=flag, action="store_false")
    versions = add("versions", "查询可恢复的历史版本")
    versions.add_argument("document_id")
    restore = add("restore", "恢复历史为新版本，历史文件保留")
    restore.add_argument("document_id")
    restore.add_argument("version", type=int)
    restore.add_argument("--expected-version", required=True, type=int)
    compare = add("compare", "比较两个版本的内容和批注")
    compare.add_argument("document_id")
    compare.add_argument("from_version", type=int)
    compare.add_argument("--to-version", type=int)
    export = add("export", "导出完整离线 HTML 包")
    export.add_argument("document_id")
    export.add_argument("--output", required=True)
    export.add_argument("--version", type=int)
    search = add("search", "搜索具体章节和内容")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    feedback = add("feedback", "读取用户批注、修改意见和个人笔记")
    feedback.add_argument("document_id")
    feedback.add_argument("--page-id")
    position = add("position", "读取或保存阅读位置")
    position.add_argument("document_id")
    position.add_argument("--page-id")
    position.add_argument("--scroll-y", type=float)
    position.add_argument("--anchor")
    settings = add("settings", "读取、更新、导入和导出统一标准与配置")
    settings.add_argument("--values", help="JSON 文件或对象；作为配置导入")
    settings.add_argument("--export", help="将读回配置导出为 JSON 文件")
    config_export = add("config-export", "导出可迁移配置")
    config_export.add_argument("--output", required=True)
    config_import = add("config-import", "导入配置，与App同步")
    config_import.add_argument("--source", required=True)
    add("reindex", "从实际版本文件重建全文搜索索引")
    add("tasks", "查询生成任务")
    for command in ("task", "cancel", "resume"):
        item = add(command, {"task": "查询任务和完成证据", "cancel": "取消自己的生成任务", "resume": "接续原任务的未完成步骤"}[command])
        item.add_argument("task_id")
    serve = add("serve", "按需启动有 bearer 鉴权的 localhost API；无常驻前提")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--token", help="可选指定本次访问 token；默认生成并保存私人 token")
    add("mcp", "MCP stdio 接入：initialize、tools/list、tools/call")
    for name in ("_worker", "_model"):
        item = add(name, argparse.SUPPRESS)
        item.add_argument("task_id")
    extractor = add("_extract", argparse.SUPPRESS)
    extractor.add_argument("source")
    return result


def _output(envelope, as_json):
    if as_json:
        print(json.dumps(envelope, ensure_ascii=False))
    elif not envelope["ok"]:
        print(envelope["error"]["message"], file=sys.stderr)
    else:
        print(json.dumps(envelope["result"], ensure_ascii=False, indent=2))


def schema():
    from .agent import content_schema
    from .mcp import tool_specs
    return {"version": "1.0", "rpc": "lumen rpc ACTION --params JSON_OR_FILE --json", "params_transport": {"inline": "JSON object", "file": "UTF-8 JSON file path", "large_payload": "使用0600参数文件，避免系统argv大小限制"}, "content_schema": content_schema(),
            "content_example": {"title": "项目解释", "home": "overview", "pages": [{"id": "overview", "title": "整体理解", "summary": "先理解用途和职责", "blocks": [{"id": "purpose", "type": "prose", "text": "解释正文"}]}]},
            "tools": tool_specs(), "gui_only": ["窗口布局和实机阅读交互"], "human_only": ["系统文件授权弹窗（出现时）"],
            "model_policy": "用户共享配置；不固定型号；已有内容使用 build 不调用模型"}


def main(argv=None):
    args = parser().parse_args(argv)
    action, state_dir = args.command, args.state_dir
    try:
        if action == "serve":
            from .api import serve
            return serve(state_dir, host=args.host, port=args.port, token=args.token)
        if action == "mcp":
            from .mcp import run_stdio
            return run_stdio(state_dir)
        if action == "_worker":
            from .jobs import run_task
            task = run_task(args.task_id, state_dir)
            return 0 if task["status"] == "succeeded" else 1
        if action == "_model":
            from .jobs import model_step
            try:
                print(json.dumps(model_step(args.task_id, state_dir), ensure_ascii=False))
                return 0
            except Exception as exc:
                error = exc.as_dict() if isinstance(exc, LumenError) else {"code": "agent_failed", "message": str(exc)}
                print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
                return 1
        if action == "_extract":
            from .agent import _extract_document_text
            try:
                text, metadata = _extract_document_text(args.source)
                print(json.dumps({"text": text, "metadata": metadata}, ensure_ascii=False))
                return 0
            except Exception as exc:
                error = exc.as_dict() if isinstance(exc, LumenError) else {"code": "source_unreadable", "message": str(exc)}
                print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
                return 1
        if action == "schema":
            envelope = {"ok": True, "action": "schema", "result": schema()}
        elif action == "agent-status":
            from .agent import provider_status
            envelope = {"ok": True, "action": "agent-status", "result": provider_status()}
        else:
            from .service import dispatch
            if action == "rpc":
                action, params = args.action, _json_value(args.params)
                if not isinstance(params, dict):
                    raise LumenError("invalid_params", "RPC 参数必须是 JSON 对象")
            else:
                params = {key: value for key, value in vars(args).items() if key not in ("command", "state_dir", "json", "wait", "export") and value is not None}
                for key in ("content", "patches", "feedback", "values"):
                    if key in params:
                        params[key] = _json_value(params[key])
                if "html" in params:
                    params["html"] = Path(params["html"]).expanduser().read_text(encoding="utf-8")
            envelope = dispatch(action, params, state_dir)
            if args.command == "generate" and args.wait and envelope["ok"]:
                task = envelope["result"]["task"]
                while task["status"] in ("queued", "running", "cancelling"):
                    time.sleep(.2)
                    current = dispatch("task", {"task_id": task["id"]}, state_dir)
                    if not current["ok"]:
                        envelope = current
                        break
                    task = current["result"]["task"]
                else:
                    envelope["result"] = {"task": task}
                    if task["status"] != "succeeded":
                        envelope = {"ok": False, "action": "generate", "error": task.get("error") or {"code": task["status"], "message": "生成未完成"}, "result": {"task": task}}
            if args.command == "settings" and args.export and envelope["ok"]:
                Path(args.export).expanduser().write_text(json.dumps(envelope["result"]["settings"], ensure_ascii=False, indent=2), encoding="utf-8")
        _output(envelope, args.json)
        return 0 if envelope["ok"] else 1
    except (LumenError, OSError, ValueError, TypeError) as exc:
        error = exc.as_dict() if isinstance(exc, LumenError) else {"code": "invalid_request", "message": str(exc)}
        _output({"ok": False, "action": action, "error": error}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
