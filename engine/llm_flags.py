"""Pure-text Codex flags from the verified official CLI integration."""
import os
import re
from pathlib import Path

def _codex_text_flags() -> list[str]:
    """Per-call tool suppression; keep the user's model, provider, login and rules.

    Pure text jobs otherwise initialize every GUI/MCP plugin and can spend the
    whole job deadline reconnecting to integrations they will never use.
    Codex CLI config overrides
    """
    flags = ["--disable", "apps", "--disable", "plugins", "--disable", "shell_tool",
             "--disable", "multi_agent", "-c", 'web_search="disabled"', "-c", "notify=[]"]
    config = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    if not config.exists():
        return flags
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 callers can use the standard backport.
        try:
            import tomli as tomllib
        except ModuleNotFoundError as exc:
            raise ConnectionError("Codex 文本调用读取配置需要 Python 3.11+ 或 tomli") from exc
    settings = tomllib.loads(config.read_text(encoding="utf-8"))
    for name in settings.get("mcp_servers", {}):
        # CLI dotted overrides treat quotes as literal characters, unlike TOML.
        # Reject ambiguous names instead of silently leaving their tools enabled.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ConnectionError("Codex MCP 名称无法用于纯文本配置覆盖：" + name)
        flags += ["-c", f"mcp_servers.{name}.enabled=false"]
    return flags
