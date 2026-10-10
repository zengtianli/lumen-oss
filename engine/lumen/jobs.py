"""Finite per-generation tasks. No daemon, polling scheduler or product queue."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import threading
import uuid

from .types import LumenError

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
MAX_ATTEMPTS = 3


def state_root(state_dir=None):
    return Path(state_dir or os.environ.get("LUMEN_STATE_DIR") or Path.home() / "Library/Application Support/Lumen").expanduser().resolve()


def _folder(state_dir=None):
    folder = state_root(state_dir) / "jobs"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def _valid_id(task_id):
    if not isinstance(task_id, str) or not task_id.startswith("gen-") or len(task_id) != 36 or any(c not in "gen-0123456789abcdef" for c in task_id):
        raise LumenError("invalid_task", "任务编号无效")
    return task_id


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, path)


@contextlib.contextmanager
def _lock(path, nonblocking=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        os.fchmod(handle.fileno(), 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError as exc:
            raise LumenError("task_busy", "该任务正在执行") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _path(task_id, state_dir=None):
    return _folder(state_dir) / _valid_id(task_id) / "task.json"


def _read(task_id, state_dir=None):
    path = _path(task_id, state_dir)
    if not path.is_file():
        raise LumenError("task_missing", "找不到生成任务", {"task_id": task_id})
    return json.loads(path.read_text(encoding="utf-8"))


def _update(task_id, values, state_dir=None):
    path = _path(task_id, state_dir)
    with _lock(path.with_suffix(".lock")):
        task = _read(task_id, state_dir)
        if task.get("cancel_requested") and values.get("status") in ("running", "succeeded"):
            values = {**values, "status": "cancelled"}
        task.update(values)
        task["updated_at"] = _now()
        _atomic(path, task)
        return task


def _identity(pid):
    try:
        process = subprocess.run(["/bin/ps", "-p", str(int(pid)), "-o", "lstart="], capture_output=True, text=True, timeout=2)
        return process.stdout.strip() if process.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _alive(task):
    pid = task.get("worker_pid")
    return bool(pid and task.get("worker_identity") and _identity(pid) == task["worker_identity"])


def _public(task):
    result = {key: value for key, value in task.items() if key not in ("current_content", "feedback", "notes")}
    if task["status"] in ("queued", "running", "cancelling") and task.get("worker_pid") and not _alive(task):
        result.update(status="cancelled" if task.get("cancel_requested") else "interrupted", message="执行进程已停止，可接续原任务")
    result["can_resume"] = result["status"] in ("failed", "cancelled", "interrupted") and task.get("attempts", 0) < MAX_ATTEMPTS
    return result


def list_tasks(state_dir=None):
    result = []
    for path in _folder(state_dir).glob("gen-*/task.json"):
        try:
            result.append(_public(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(result, key=lambda row: row["created_at"], reverse=True)


def _worker_command():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "entry.py")]


def launch_task(task_id, state_dir=None):
    folder = _path(task_id, state_dir).parent
    with _lock(folder / "launch.lock", nonblocking=True):
        task = _read(task_id, state_dir)
        if task["status"] == "succeeded":
            return _public(task)
        if _alive(task) and task["status"] not in TERMINAL:
            raise LumenError("task_busy", "该任务正在执行")
        return _launch_task(task_id, state_dir)


def _launch_task(task_id, state_dir=None):
    folder = _path(task_id, state_dir).parent
    log = folder / "worker.log"
    try:
        with log.open("a", encoding="utf-8") as output:
            log.chmod(0o600)
            worker = subprocess.Popen([*_worker_command(), "--state-dir", str(state_root(state_dir)), "_worker", task_id],
                                      stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                      cwd=folder, start_new_session=True)
    except OSError as exc:
        error = {"code": "worker_launch_failed", "message": "生成执行器未能启动", "details": {"reason": str(exc)}}
        _update(task_id, {"status": "failed", "error": error}, state_dir)
        raise LumenError(error["code"], error["message"], error["details"]) from exc
    _update(task_id, {"worker_pid": worker.pid, "worker_identity": _identity(worker.pid)}, state_dir)
    # Long-lived API/MCP hosts reap their finite children; short-lived CLI hosts
    # may exit immediately and the system adopts the detached worker.
    threading.Thread(target=worker.wait, daemon=True).start()
    return _public(_read(task_id, state_dir))


def create_task(params, state_dir=None, *, launch=True):
    from .agent import snapshot_source
    from .settings import settings, validate_generation_value
    source, request = params.get("source"), params.get("request")
    if not source or not isinstance(request, str) or not request.strip():
        raise LumenError("invalid_request", "生成需要来源路径和解释要求")
    defaults = settings(state_root(state_dir))["settings"]
    kind = defaults["default_kind"] if params.get("kind") is None else params["kind"]
    if kind not in ("portal", "report", "teach"):
        raise LumenError("invalid_kind", "生成类型必须是 portal、report 或 teach")
    timeout = defaults["generation"]["timeout_seconds"] if params.get("timeout_seconds") is None else params["timeout_seconds"]
    validate_generation_value("timeout_seconds", timeout, error_code="invalid_timeout", numeric_timeout=True)
    source_budget = defaults["generation"]["max_source_bytes"] if params.get("max_source_bytes") is None else params["max_source_bytes"]
    validate_generation_value("max_source_bytes", source_budget, error_code="invalid_source_budget")
    provider = params.get("provider")
    if provider not in (None, "codex", "claude"):
        raise LumenError("invalid_provider", "模型提供方必须是 codex、claude 或默认")
    request_key = params.get("idempotency_key")
    if request_key is not None and (not isinstance(request_key, str) or not request_key or len(request_key) > 200):
        raise LumenError("invalid_request", "idempotency_key 必须是非空短字符串")
    root = _folder(state_dir)
    request_params = {k: v for k, v in params.items() if k not in ("wait", "idempotency_key")
                      and not (k in ("kind", "timeout_seconds", "max_source_bytes") and v is None)}
    fingerprint = hashlib.sha256(json.dumps(request_params, sort_keys=True).encode()).hexdigest()
    admission_key = hashlib.sha256((request_key or uuid.uuid4().hex).encode()).hexdigest()
    with _lock(root / "admission" / (admission_key + ".lock")):
        if request_key:
            for row in list_tasks(state_dir):
                if row.get("idempotency_key") == request_key:
                    if row.get("request_fingerprint") != fingerprint:
                        raise LumenError("idempotency_conflict", "同一个请求键对应不同输入")
                    return row
        deadline_at = time.time() + timeout
        snapshot = snapshot_source(source, timeout_seconds=min(30, timeout), max_source_bytes=source_budget)
        task_id = "gen-" + uuid.uuid4().hex
        folder = root / task_id
        folder.mkdir(mode=0o700)
        _atomic(folder / "source.json", snapshot)
        clean_params = {k: v for k, v in params.items() if k not in ("wait", "idempotency_key")}
        clean_params.update(source=snapshot["source"], request=request.strip(), kind=kind,
                            timeout_seconds=timeout, max_source_bytes=source_budget)
        task = {"id": task_id, "task_id": task_id, "status": "queued", "step": "snapshot", "created_at": _now(), "updated_at": _now(),
                "params": clean_params, "scope": "one_html_document", "steps": ["snapshot", "generate", "build", "verify"],
                "timeout_seconds": timeout, "deadline_at": deadline_at, "stall_timeout": min(timeout, 300),
                "attempts": 0, "max_attempts": MAX_ATTEMPTS, "checkpoint": "snapshot", "cancel_requested": False,
                "source_sha256": snapshot["sha256"], "snapshot_path": str(folder / "source.json"),
                "idempotency_key": request_key, "request_fingerprint": fingerprint}
        if params.get("document_id"):
            from .service import dispatch
            envelope = dispatch("get", {"document_id": params["document_id"]}, state_dir)
            if not envelope["ok"]:
                raise LumenError(envelope["error"]["code"], envelope["error"]["message"])
            current = envelope["result"]
            expected = params.get("expected_version")
            if expected is None or expected != current["document"]["version"]:
                raise LumenError("version_conflict", "更新生成必须提供当前版本", {"current_version": current["document"]["version"]})
            task.update(current_content=current.get("content"), feedback=current.get("feedback", {}), notes=current.get("notes", ""))
        _atomic(folder / "task.json", task)
    return launch_task(task_id, state_dir) if launch else _public(task)


def _runtime():
    try:
        import task_runtime
        return task_runtime
    except ModuleNotFoundError as exc:
        if exc.name != "task_runtime":
            raise
    raise LumenError("runtime_missing", "The bundled task runtime is missing")


def _stop_model(child):
    _runtime().terminate_tree(child)


def _model(task, folder, state_dir):
    runtime = _runtime()
    output_path, error_path = folder / "model_output.log", folder / "model_error.log"
    with output_path.open("w", encoding="utf-8") as output, error_path.open("w", encoding="utf-8") as error:
        output_path.chmod(0o600)
        error_path.chmod(0o600)
        # Helper shares worker's group, so Chapter can inspect and terminate its
        # descendants including canonical CLI processes in their own groups.
        helper = subprocess.Popen([*_worker_command(), "--state-dir", str(state_root(state_dir)), "_model", task["id"]],
                                  stdin=subprocess.DEVNULL, stdout=output, stderr=error, text=True)
        _update(task["id"], {"model_pid": helper.pid}, state_dir)
        try:
            runtime.wait_process(helper, timeout=max(.01, task["deadline_at"] - time.time()),
                                 cancelled=lambda: _read(task["id"], state_dir).get("cancel_requested", False),
                                 progress=lambda: output_path.stat().st_size + error_path.stat().st_size,
                                 stall_timeout=task.get("stall_timeout", task["timeout_seconds"]))
            if helper.returncode != 0:
                detail_text = error_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                try:
                    detail = json.loads(detail_text.strip().splitlines()[-1])
                    raise LumenError(detail.get("code", "agent_failed"), detail.get("message", "模型生成失败"), detail.get("details"))
                except (ValueError, IndexError):
                    raise LumenError("agent_failed", "模型生成失败", {"reason": detail_text})
            if not (folder / "draft.json").is_file():
                raise LumenError("draft_missing", "模型进程没有产生内容草稿")
        except runtime.TaskStopped as exc:
            _stop_model(helper)
            messages = {"cancelled": "生成已取消", "stalled": "模型输出超过停滞预算", "deadline_exceeded": "生成达到时间预算"}
            raise LumenError(exc.reason, messages.get(exc.reason, "生成执行已停止")) from exc
        finally:
            if helper.poll() is None:
                _stop_model(helper)


def model_step(task_id, state_dir=None):
    from .agent import generate_content
    task = _read(task_id, state_dir)
    folder = _path(task_id, state_dir).parent
    remaining = task["deadline_at"] - time.time()
    if remaining <= 0:
        raise LumenError("deadline_exceeded", "生成达到时间预算")
    snapshot = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    result = generate_content(task, snapshot, max(.1, remaining))
    _atomic(folder / "draft.json", result["content"])
    _atomic(folder / "model_receipt.json", result.get("receipt", {}))
    return {"draft_path": str(folder / "draft.json")}


def run_task(task_id, state_dir=None, *, model_runner=None, dispatcher=None):
    from .service import dispatch
    dispatcher = dispatcher or dispatch
    folder = _path(task_id, state_dir).parent
    with _lock(folder / "execution.lock", nonblocking=True):
        task = _read(task_id, state_dir)
        if task["status"] == "succeeded":
            return _public(task)
        if task.get("cancel_requested"):
            return _public(_update(task_id, {"status": "cancelled"}, state_dir))
        if task["attempts"] >= MAX_ATTEMPTS:
            raise LumenError("attempts_exhausted", "生成已达到有限尝试预算")
        task = _update(task_id, {"status": "running", "attempts": task["attempts"] + 1, "worker_pid": os.getpid(),
                               "worker_identity": _identity(os.getpid()), "started_at": _now(), "error": None}, state_dir)
        try:
            if time.time() >= task["deadline_at"]:
                raise LumenError("deadline_exceeded", "生成达到时间预算")
            if not (folder / "draft.json").is_file():
                _update(task_id, {"step": "generate"}, state_dir)
                (model_runner or _model)(task, folder, state_dir)
            task = _read(task_id, state_dir)
            if task.get("cancel_requested"):
                raise LumenError("cancelled", "生成已取消")
            if time.time() >= task["deadline_at"]:
                raise LumenError("deadline_exceeded", "生成达到时间预算")
            draft = json.loads((folder / "draft.json").read_text(encoding="utf-8"))
            task = _update(task_id, {"checkpoint": "draft", "step": "build"}, state_dir)
            result_path = folder / "build_result.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                params = {k: task["params"][k] for k in ("kind", "document_id", "expected_version", "project", "tags") if k in task["params"]}
                params.update(content=draft, sources=[{"path": task["params"]["source"], "sha256": task["source_sha256"], "task_id": task_id}],
                              idempotency_key="generate:" + task_id)
                envelope = dispatcher("build", params, state_dir)
                if not envelope.get("ok"):
                    error = envelope["error"]
                    raise LumenError(error["code"], error["message"], error.get("details"))
                result = envelope["result"]
                _atomic(result_path, result)
            task = _update(task_id, {"checkpoint": "built", "step": "verify", "document_id": result["document"]["id"]}, state_dir)
            if time.time() >= task["deadline_at"]:
                raise LumenError("deadline_exceeded", "生成达到时间预算")
            envelope = dispatcher("get", {"document_id": result["document"]["id"]}, state_dir)
            if not envelope.get("ok") or not Path(result["document"]["entry_path"]).is_file() or not result.get("validation", {}).get("ok"):
                raise LumenError("verification_failed", "生成文件或知识库读回未通过")
            if envelope["result"]["document"]["version"] < result["document"]["version"]:
                raise LumenError("verification_failed", "知识库没有读回生成版本")
            if time.time() >= task["deadline_at"]:
                raise LumenError("deadline_exceeded", "生成达到时间预算")
            task = _update(task_id, {"status": "succeeded", "step": "done", "checkpoint": "verified", "completed_at": _now(),
                                   "result": result, "model_pid": None}, state_dir)
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, LumenError) else {"code": "generation_failed", "message": str(exc)}
            task = _update(task_id, {"status": "cancelled" if error["code"] == "cancelled" else "failed", "error": error,
                                   "completed_at": _now(), "model_pid": None}, state_dir)
        return _public(task)


def cancel_task(task_id, state_dir=None):
    task = _read(task_id, state_dir)
    if task["status"] in TERMINAL:
        return _public(task)
    status = "cancelling" if _alive(task) or task.get("model_pid") else "cancelled"
    task = _update(task_id, {"cancel_requested": True, "status": status, "message": "已请求取消，执行器会收束本任务模型进程"}, state_dir)
    return _public(task)


def resume_task(task_id, state_dir=None, *, launch=True):
    path = _path(task_id, state_dir)
    with _lock(path.parent / "launch.lock", nonblocking=True):
        with _lock(path.parent / "execution.lock", nonblocking=True):
            task = _read(task_id, state_dir)
            if task["status"] == "succeeded":
                return _public(task)
            if _alive(task) and task["status"] not in TERMINAL:
                raise LumenError("task_busy", "该任务正在执行")
            if task["attempts"] >= MAX_ATTEMPTS:
                raise LumenError("attempts_exhausted", "生成已达到有限尝试预算")
            task = _update(task_id, {"status": "queued", "cancel_requested": False, "deadline_at": time.time() + task["timeout_seconds"],
                                   "worker_pid": None, "worker_identity": None, "error": None}, state_dir)
        return _launch_task(task_id, state_dir) if launch else _public(task)


def dispatch_job(action, params=None, state_dir=None):
    params = params or {}
    if action == "generate":
        return {"task": create_task(params, state_dir)}
    if action == "tasks":
        return {"tasks": list_tasks(state_dir)}
    task_id = params.get("task_id")
    if action == "task":
        return {"task": _public(_read(task_id, state_dir))}
    if action == "cancel":
        return {"task": cancel_task(task_id, state_dir)}
    if action == "resume":
        return {"task": resume_task(task_id, state_dir)}
    raise LumenError("unknown_action", "未知的生成任务操作")
