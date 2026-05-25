"""Translator agents that bid on-chain for the right to translate news events.

Four reference agents ship by default — Gemini, DeepSeek, Qwen, Llama — each
with a distinct bid strategy. ``BaseTranslatorAgent`` provides the shared
pipeline + chain plumbing.
"""

from .base import BaseTranslatorAgent
from .deepseek_agent import DeepSeekAgent
from .gemini_agent import GeminiAgent
from .llama_agent import LlamaAgent
from .qwen_agent import QwenAgent

AGENT_REGISTRY: dict[str, type[BaseTranslatorAgent]] = {
    "gemini": GeminiAgent,
    "deepseek": DeepSeekAgent,
    "qwen": QwenAgent,
    "llama": LlamaAgent,
}

__all__ = [
    "AGENT_REGISTRY",
    "BaseTranslatorAgent",
    "DeepSeekAgent",
    "GeminiAgent",
    "LlamaAgent",
    "QwenAgent",
]
