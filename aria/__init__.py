"""
A.R.I.A. Adaptive Robotic Intelligence Agent OS.
Modular Agent Framework.
"""

from aria.config import (
    ROOT_DIR, WORKSPACE_DIR, SOUL_PATH,
    ARIA_SOUL, BRIDGE_TOKEN, add_log
)
import aria.config as config
import aria.memory as memory
import aria.speech as speech
import aria.hardware as hardware
import aria.vision as vision
import aria.scheduler as scheduler
import aria.spotify as spotify
import aria.hud as hud
import aria.bridge as bridge
import aria.tools as tools
import aria.agent as agent

from aria.agent.brain import run_agent
from aria.speech import speak, interrupt_speech
from aria.tools.dispatch import execute_tool, load_toolkit

__all__ = [
    "config",
    "memory",
    "speech",
    "hardware",
    "vision",
    "scheduler",
    "spotify",
    "hud",
    "bridge",
    "tools",
    "agent",
    "run_agent",
    "speak",
    "interrupt_speech",
    "execute_tool",
    "load_toolkit",
    "add_log",
    "ARIA_SOUL",
    "WORKSPACE_DIR",
]


from aria.main import main, start_all, handle_action
