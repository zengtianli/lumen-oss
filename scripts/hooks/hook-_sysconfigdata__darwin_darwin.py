"""Keep CPython configuration while removing the build machine's prefix."""
from pathlib import Path
import sys
import types


def hook(hook_api):
    prefixes = sorted({sys.prefix, sys.base_prefix}, key=len, reverse=True)

    def portable(value):
        if isinstance(value, types.CodeType):
            return value.replace(co_consts=tuple(portable(item) for item in value.co_consts))
        if isinstance(value, str):
            for prefix in prefixes:
                value = value.replace(prefix, "/usr/local")
            return value.replace(str(Path.home()), "~")
        if isinstance(value, tuple):
            return tuple(portable(item) for item in value)
        return value

    hook_api.module.code = portable(hook_api.module.code)
