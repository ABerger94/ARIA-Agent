"""Replay and resource policy. Unknown tools are never automatically replayed."""
READ_ONLY = frozenset({
    'get_time', 'web_search', 'fetch_url', 'read_file', 'find_file',
    'search_memory', 'read_notes', 'list_workspace', 'list_windows',
    'get_system_status', 'read_screen', 'describe_camera', 'vision_look',
    'list_price_watches', 'clipboard_read', 'task_status', 'mission_list',
    'get_approval_mode', 'list_pending_approvals', 'research', 'mtg_card',
})
DESKTOP = frozenset({
    'open_app_or_url', 'browser_agent', 'browser_control', 'click', 'type_text',
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


def retry_safe(tool):
    return tool in READ_ONLY or tool in _REGISTERED_READS


FILE_ARGUMENTS = {
    'read_file': ('filename',), 'write_file': ('filename',),
    'file_organize': ('directory',), 'file_find_advanced': ('directory',),
    'file_duplicates': ('directory',), 'disk_usage': ('directory',),
    'process_file': ('path',), 'vision_look': ('image_path',),
    'github_push_file': ('filepath',),
}
