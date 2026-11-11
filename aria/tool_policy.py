"""Replay and resource policy. Unknown tools are never automatically replayed."""
READ_ONLY = frozenset({
    'get_time', 'web_search', 'fetch_url', 'read_file', 'find_file',
    'search_memory', 'read_notes', 'list_workspace', 'list_windows',
    'get_system_status', 'read_screen', 'describe_camera', 'vision_look',
    'list_price_watches', 'clipboard_read', 'task_status', 'mission_list',
    'get_approval_mode', 'list_pending_approvals', 'mtg_card',
    # NOTE (pass 36 #36): 'research' used to sit here, but the tool
    # is action-dependent — list/read are reads, run files a brief
    # and an index entry. See retry_safe() below; a bare name can
    # never answer "is this research call replay-safe".
})
DESKTOP = frozenset({
    'open_app_or_url', 'launch_app', 'browser_agent', 'browser_control', 'click', 'type_text',
    'gui_click', 'gui_type', 'window_snap',
    'press_key', 'hotkey', 'mouse_click', 'mouse_move', 'scroll',
    'close_window', 'focus_window', 'minimize_window', 'maximize_window',
    'phone_tap', 'phone_swipe', 'phone_type', 'phone_key', 'desktop_control',
})


_REGISTERED_READS = set()


def declare_replay_safe(tool, safe):
    if safe:
        _REGISTERED_READS.add(tool)
    else:
        _REGISTERED_READS.discard(tool)


def retry_safe(tool, args=None):
    """True when re-running this tool (with these args) is safe.

    Pass 36 #36: 'research' is classified by ACTION, not by name.
    list/read are replay-safe reads; run files a brief and an index
    entry, so a mission-repair re-run would file a second brief —
    it is effectful. Callers that pass no args (missions, grader,
    rubrics) get the conservative answer for research: not safe,
    because the action is unknown there. Dispatch passes the real
    args, so a list/read research call keeps its replay-safe
    behaviour there.
    """
    if tool in READ_ONLY or tool in _REGISTERED_READS:
        return True
    if tool == "research":
        action = ""
        if isinstance(args, dict):
            action = str(args.get("action") or "").strip().lower()
        return action in ("list", "read")
    return False


# Pass 36 #14: the read-only capability set the LOCAL model gets
# under OLLAMA_SAFE_MODE (docs/PROVIDER_FALLBACK_SPEC.md §8).
# Declaration filtering is by NAME (schemas.py); dispatch
# enforcement is args-aware (research list/read are reads, run is
# an effect), so the two predicates differ by exactly that tool.
def safe_mode_declared(tool) -> bool:
    """Name-level predicate for declaration filtering: the tool
    may be OFFERED to the local model under safe mode."""
    return (tool in READ_ONLY or tool in ("load_toolkit", "research"))


def safe_mode_allowed(tool, args=None) -> bool:
    """Args-aware predicate for dispatch enforcement: the call may
    RUN under safe mode. research run writes a brief + index
    entry, so it is refused even though the tool is declared."""
    if tool in READ_ONLY or tool == "load_toolkit":
        return True
    if tool == "research":
        return retry_safe(tool, args)
    return False


FILE_ARGUMENTS = {
    'read_file': ('filename',), 'write_file': ('filename',),
    'file_organize': ('directory',), 'file_find_advanced': ('directory',),
    'file_duplicates': ('directory',), 'disk_usage': ('directory',),
    'process_file': ('path',), 'vision_look': ('image_path',),
    # NOTE (pass 26 B5#8): github_push_file is deliberately absent —
    # its `filepath` is a REMOTE repo path (URL-quoted into
    # /repos/{repo}/contents/{path} by tool_github_push), never a
    # local file, so the dispatch file_roots gate must not apply to
    # it. GitHub token + repo scoping is the authorization boundary.
    # Pass 25: code_helper's file_path used to bypass the root
    # check entirely (it was absent here), so the helper could
    # read any file on disk when file_roots were configured.
    'code_helper': ('file_path',),
}
