"""The sole business API shared by native UI, CLI, HTTP, and MCP."""
from . import __version__
from .store import Store
from .types import LumenError


ACTIONS = ["status", "list", "get", "build", "import", "save", "metadata", "versions", "compare", "restore", "search", "feedback", "position", "settings", "export", "config-export", "config-import", "reindex", "convert", "guide", "discover", "generate", "task", "tasks", "cancel", "resume"]


def dispatch(action, params=None, state_dir=None):
    params = {} if params is None else params
    try:
        if not isinstance(params, dict):
            raise LumenError("invalid_params", "参数必须为对象")
        store = Store(state_dir)
        if action in ("generate", "task", "tasks", "cancel", "resume"):
            from .jobs import dispatch_job
            result = dispatch_job(action, params, str(store.root))
        elif action == "status":
            from .settings import settings
            try:
                from .jobs import list_tasks
                tasks = list_tasks(str(store.root))
            except ImportError:
                tasks = []
            result = {"version": __version__, "state_dir": str(store.root), "documents": len(store.list(archived=False)["documents"]), "archived": len(store.list(archived=True)["documents"]), "tasks": tasks, "settings": settings(store.root)["settings"], "modes": [{"id": "portal", "title": "门户"}, {"id": "report", "title": "阅读与报告"}, {"id": "teach", "title": "教学"}]}
        elif action == "settings":
            from .settings import settings
            result = settings(store.root, **params)
        elif action in ("config-export", "config-import"):
            from .settings import export_config, import_config
            result = (export_config if action == "config-export" else import_config)(store.root, **params)
        elif action == "discover":
            from .discovery import discover
            result = discover(state_dir=str(store.root), **params)
        elif action == "guide":
            from .guides import guide
            result = guide(**params)
        elif action == "convert":
            from .converters import convert
            result = convert(state_dir=str(store.root), **params)
        elif action == "reindex":
            result = store.rebuild_index()
        else:
            method = {"import": "import_document", "save": "save_document"}.get(action, action)
            if action not in ACTIONS or not callable(getattr(store, method, None)):
                raise LumenError("unknown_action", "未知操作", {"action": action, "actions": ACTIONS})
            result = getattr(store, method)(**params)
        return {"ok": True, "action": action, "result": result}
    except LumenError as error:
        return {"ok": False, "action": action, "error": error.as_dict()}
    except (TypeError, ValueError, KeyError) as error:
        return {"ok": False, "action": action, "error": {"code": "invalid_params", "message": str(error)}}
    except OSError as error:
        return {"ok": False, "action": action, "error": {"code": "io_error", "message": str(error)}}
