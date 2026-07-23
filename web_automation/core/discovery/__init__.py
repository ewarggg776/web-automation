"""Core discovery package - generic dataclasses, merger, scheduler, and models."""

from web_automation.core.discovery.models import (
    DiscoveredItem,
    ScoredItem,
    AbstractSource,
)
from web_automation.core.discovery.merger import GenericMerger, ItemRegistry
from web_automation.core.discovery.scheduler import GenericScheduler, CooldownManager

__all__ = [
    "DiscoveredItem",
    "ScoredItem", 
    "AbstractSource",
    "GenericMerger",
    "ItemRegistry",
    "GenericScheduler",
    "CooldownManager",
]