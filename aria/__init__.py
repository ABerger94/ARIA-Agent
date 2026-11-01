"""ARIA public API, loaded on demand for light companion and diagnostic CLIs."""
from importlib import import_module

_MODULES = {'config', 'memory', 'speech', 'hardware', 'vision', 'scheduler',
            'spotify', 'hud', 'bridge', 'tools', 'agent'}
_EXPORTS = {
    **{key: ('aria.config', key) for key in ('ROOT_DIR', 'WORKSPACE_DIR', 'SOUL_PATH',
        'ARIA_SOUL', 'BRIDGE_TOKEN', 'add_log')},
    'run_agent': ('aria.agent.brain', 'run_agent'),
    'speak': ('aria.speech', 'speak'), 'interrupt_speech': ('aria.speech', 'interrupt_speech'),
    'execute_tool': ('aria.tools.dispatch', 'execute_tool'),
    'load_toolkit': ('aria.tools.dispatch', 'load_toolkit'),
    'main': ('aria.main', 'main'), 'start_all': ('aria.main', 'start_all'),
    'handle_action': ('aria.main', 'handle_action'),
}
__all__ = sorted(_MODULES | _EXPORTS.keys())


def __getattr__(name):
    if name in _MODULES:
        return import_module('aria.' + name)
    if name in _EXPORTS:
        module, attribute = _EXPORTS[name]
        return getattr(import_module(module), attribute)
    raise AttributeError(name)
