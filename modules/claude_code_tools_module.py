"""Per-book opt-in: let the claudecode translation model look things up.

When on, a translation call made through the `claudecode` provider is given
the read-only T9 MCP
server (t9-mcp-readonly.service): the book's glossary, note history, other
chapters, footnotes. The model is told which book and chapter it is on and to
look up only what the prompt's glossary does not already settle.

Auto follows the global `claude_code_mcp_tools` setting (default off); a book
set to On or Off overrides it either way — the module system's usual tri-state.

No hooks: the engine asks module_config() whether this book has it on and
passes the book to the provider. Other providers ignore it.
"""
import os

from .base import TranslationModule

MODULE_ID = "claude_code_tools"
DEFAULT_MAX_TURNS = 8


class ClaudeCodeToolsModule(TranslationModule):
    id = MODULE_ID
    name = "Claude Code research tools"
    description = ("Gives the claudecode translation model read-only lookup tools "
                   "(glossary, note history, other chapters) while it translates. "
                   "Only affects books translated with the claudecode provider.")
    auto_url_patterns = []
    default_enabled = False
    has_auto = True

    def auto_enabled(self, book, ctx):
        """Auto = the global setting (Settings → Translation Safeguards)."""
        config = (ctx or {}).get("config")
        if config is not None and hasattr(config, "claude_code_mcp_tools"):
            return bool(config.claude_code_mcp_tools)
        return os.getenv("CLAUDE_CODE_MCP_TOOLS", "0").lower() in ("1", "true", "yes")

    @property
    def auto_hint(self):
        return "follows the global 'Let the claudecode model use lookup tools' setting"

    settings_schema = [
        {
            "key": "max_turns",
            "type": "number",
            "label": "Max model turns per call",
            "help": "Caps tool round-trips per translation call (each lookup is one "
                    "turn; the answer is one more). A call that hits the cap fails "
                    "and is retried like any other failed chunk.",
            "default": DEFAULT_MAX_TURNS,
        },
    ]
