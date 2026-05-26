"""Translator agents that bid on-chain for the right to translate news events.

Three reference seeders ship by default — ``SeederAlpha`` (macro),
``SeederBeta`` (geopolitics) and ``SeederGamma`` (markets / sentiment).
They all run on the same Anthropic Haiku snapshot but differ by persona
prompt, temperature, and bid strategy, so external operators see a
provider-agnostic seeder fleet.

``BaseTranslatorAgent`` provides the shared pipeline + chain plumbing.
The legacy class names (``GeminiAgent`` / ``DeepSeekAgent`` /
``QwenAgent``) are re-exported as aliases so existing imports continue
to work during the migration.
"""

from .base import BaseTranslatorAgent
from .deepseek_agent import DeepSeekAgent, SeederBeta
from .gemini_agent import GeminiAgent, SeederAlpha
from .qwen_agent import QwenAgent, SeederGamma

# Registry keyed by the on-disk wallet slot name (NOT the display name) so
# deterministic wallet derivation, persisted bid records, and the
# orchestrator's agent_names tuple stay stable across the rename.
AGENT_REGISTRY: dict[str, type[BaseTranslatorAgent]] = {
    "gemini": SeederAlpha,
    "deepseek": SeederBeta,
    "qwen": SeederGamma,
}

# Tuple form used by call sites that want to iterate the seeder classes
# directly (e.g. test harnesses).
SEEDER_AGENTS: tuple[type[BaseTranslatorAgent], ...] = (
    SeederAlpha,
    SeederBeta,
    SeederGamma,
)

__all__ = [
    "AGENT_REGISTRY",
    "BaseTranslatorAgent",
    "DeepSeekAgent",
    "GeminiAgent",
    "QwenAgent",
    "SEEDER_AGENTS",
    "SeederAlpha",
    "SeederBeta",
    "SeederGamma",
]
