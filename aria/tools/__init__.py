"""
ARIA Tools Package.
Unified tool execution, schemas, progressive toolkits, and bounded skills.
"""

from aria.tools.schemas import (
    ALL_FUNCTION_DECLARATIONS,
    TOOLS_DECLARATION,
    TOOLKITS,
    COMMAND_GUIDE,
    LOAD_TOOLKIT_DECLARATION,
    get_tool_decls_by_name,
    get_toolkit_declarations,
    get_toolkits_prompt_block,
)
from aria.tools.sandbox import (
    RISKY_TOOLS,
    call_signature,
    missing_required_args,
    target_mentioned,
    stale_target_check,
    risky_description,
    truncate_output,
    tool_run_python,
)
from aria.tools.skills import (
    SkillBudgetExceeded,
    SKILLS,
    tool_run_skill,
    skill_deep_research,
    skill_system_check,
    skill_file_sweep,
)
from aria.tools.dispatch import (
    execute_tool,
    register_tool,
    get_registered_tools,
    tool_load_toolkit,
    reset_toolkits,
    get_loaded_toolkits,
    get_active_declarations,
    get_toolkits_prompt,
    reset_turn_state,
    set_turn_context,
    get_last_tool_executed,
    set_spine_hook,
    set_hud_hook,
    set_log_hook,
    set_history_hook,
)
import aria.tools.builtins as builtins

__all__ = [
    "execute_tool",
    "register_tool",
    "get_registered_tools",
    "tool_load_toolkit",
    "reset_toolkits",
    "get_loaded_toolkits",
    "get_active_declarations",
    "get_toolkits_prompt",
    "reset_turn_state",
    "set_turn_context",
    "get_last_tool_executed",
    "set_spine_hook",
    "set_hud_hook",
    "set_log_hook",
    "set_history_hook",
    "ALL_FUNCTION_DECLARATIONS",
    "TOOLS_DECLARATION",
    "TOOLKITS",
    "COMMAND_GUIDE",
    "LOAD_TOOLKIT_DECLARATION",
    "get_tool_decls_by_name",
    "get_toolkit_declarations",
    "get_toolkits_prompt_block",
    "RISKY_TOOLS",
    "call_signature",
    "missing_required_args",
    "target_mentioned",
    "stale_target_check",
    "risky_description",
    "truncate_output",
    "tool_run_python",
    "SkillBudgetExceeded",
    "SKILLS",
    "tool_run_skill",
    "builtins",
]
