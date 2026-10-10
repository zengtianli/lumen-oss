"""Small bounded process primitives; no queue, business state or scheduling."""
from __future__ import annotations
import contextlib
import ctypes
import datetime as dt
import os
import queue
import subprocess
import struct
import sys
import threading
import time


class TaskStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def remaining(deadline_at, default=1800):
    if not deadline_at:
        return default
    deadline = dt.datetime.fromisoformat(deadline_at)
    return max(0, (deadline - dt.datetime.now(deadline.tzinfo)).total_seconds())


def terminate(child):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(child.pid, 15)
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(child.pid, 9)
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=1)
    # The leader may exit before a grandchild that ignored TERM.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(child.pid, 9)


def cleanup_group(child):
    """A completed finite command must not leave an untracked process in its group."""
    if child.pid != os.getpgrp():
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(child.pid, 9)


def process_info(pid):
    """macOS libproc identity: parent, microsecond start, group and status; no global scan."""
    if sys.platform != "darwin":
        return None
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    buffer = ctypes.create_string_buffer(136)  # SDK struct proc_bsdinfo / PROC_PIDTBSDINFO (3)
    if library.proc_pidinfo(int(pid), 3, ctypes.c_uint64(0), buffer, len(buffer)) != len(buffer):
        return None
    raw = buffer.raw
    return (struct.unpack_from("=I", raw, 16)[0], struct.unpack_from("=Q", raw, 120)[0],
            struct.unpack_from("=Q", raw, 128)[0], struct.unpack_from("=I", raw, 100)[0], struct.unpack_from("=I", raw, 4)[0])


def terminate_tree(child):
    """Clean a child in our own group without signalling siblings or this process.

    Two bounded snapshots bind each descendant's parent and start identity. Children
    in separate groups are handled by the normal group primitive.
    """
    try:
        if os.getpgid(child.pid) == child.pid:
            terminate(child)
            return
    except ProcessLookupError:
        return
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        saved, depth, pending = {}, {child.pid: 0}, [child.pid]
        while pending:
            pid = pending.pop(0)
            identity = process_info(pid)
            if identity is None:
                continue
            saved[pid] = identity
            children = (ctypes.c_int * 4096)()
            count = library.proc_listchildpids(pid, children, ctypes.sizeof(children))
            for descendant in children[:max(0, min(count, len(children)))]:
                if descendant > 0 and descendant not in depth:
                    depth[descendant] = depth[pid] + 1
                    pending.append(descendant)
        for pid in sorted(saved, key=depth.get, reverse=True):
            current = process_info(pid)
            if current is not None and current[:4] == saved[pid][:4] and pid != os.getpid():
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, 9)
        with contextlib.suppress(ProcessLookupError):
            child.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=1)
        return
    def snapshot():
        result = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,lstart="], capture_output=True, text=True, timeout=1)
        rows = {}
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
                rows[int(fields[0])] = (int(fields[1]), fields[2])
        return rows
    try:
        original = snapshot()
        depths = {child.pid: 0}
        while True:
            added = {pid: depths[parent] + 1 for pid, (parent, _) in original.items() if parent in depths and pid not in depths}
            if not added:
                break
            depths.update(added)
        current = snapshot()
        for pid in sorted(depths, key=depths.get, reverse=True):
            if original.get(pid) != current.get(pid) or pid == os.getpid():
                continue
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if os.getpgid(pid) == os.getpgrp():
                    os.kill(pid, 9)
    finally:
        with contextlib.suppress(ProcessLookupError):
            child.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=1)


def write_input(child, text, *, timeout, cancelled=lambda: False):
    """Do not let a CLI that never reads stdin block the worker before stdout is observed."""
    finished = threading.Event()
    def write():
        try:
            with contextlib.suppress(BrokenPipeError):
                child.stdin.write(text)
        finally:
            with contextlib.suppress(BrokenPipeError):
                child.stdin.close()
            finished.set()
    threading.Thread(target=write, daemon=True).start()
    deadline = time.monotonic() + timeout
    while not finished.wait(.05):
        if cancelled() or time.monotonic() >= deadline:
            terminate(child)
            raise TaskStopped("cancelled" if cancelled() else "deadline_exceeded")


def iter_output(child, *, timeout, stall_timeout=300, cancelled=lambda: False):
    """A reader cannot hold the caller past its budget, even with an open pipe."""
    lines = queue.Queue()
    def read():
        try:
            for line in child.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    deadline, activity = time.monotonic() + timeout, time.monotonic()
    try:
        while True:
            if cancelled():
                raise TaskStopped("cancelled")
            now = time.monotonic()
            if now >= deadline:
                raise TaskStopped("deadline_exceeded")
            if now - activity >= stall_timeout:
                raise TaskStopped("stalled")
            try:
                line = lines.get(timeout=min(.25, max(.001, deadline - now)))
            except queue.Empty:
                continue
            if line is None:
                break
            activity = time.monotonic()
            yield line
        wait_process(child, timeout=max(0, deadline - time.monotonic()), cancelled=cancelled)
    except TaskStopped:
        terminate(child)
        raise


def wait_process(child, *, timeout, cancelled=lambda: False, progress=None, stall_timeout=None):
    deadline, activity, previous = time.monotonic() + timeout, time.monotonic(), None
    while True:
        if cancelled():
            terminate(child)
            raise TaskStopped("cancelled")
        now = time.monotonic()
        if progress:
            current = progress()
            if current != previous:
                activity, previous = now, current
        if now >= deadline or stall_timeout and now - activity >= stall_timeout:
            terminate(child)
            raise TaskStopped("deadline_exceeded" if now >= deadline else "stalled")
        try:
            return child.wait(timeout=min(.25, deadline - now))
        except subprocess.TimeoutExpired:
            continue


def communicate_bounded(child, input=None, *, timeout, cancelled=lambda: False):
    deadline, first = time.monotonic() + timeout, True
    while True:
        if cancelled() or time.monotonic() >= deadline:
            terminate(child)
            raise TaskStopped("cancelled" if cancelled() else "deadline_exceeded")
        try:
            return child.communicate(input=input if first else None, timeout=min(.25, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            first = False
