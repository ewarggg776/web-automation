"""Web Automation Framework - Generic web automation for discovery, validation, and monitoring."""

from web_automation.core.discovery import (
    DiscoveredItem,
    ScoredItem,
    AbstractSource,
    GenericMerger,
    GenericScheduler,
)
from web_automation.core.validators import (
    HTTPValidator,
    CaptchaDetector,
    SelectorProbe,
    ContentExtractor,
)
from web_automation.core.storage import (
    StateDatabase,
    EncryptedConfig,
    BudgetManager,
)
from web_automation.core.runner import AutomationRunner

__version__ = "0.1.0"

__all__ = [
    "DiscoveredItem",
    "ScoredItem", 
    "AbstractSource",
    "GenericMerger",
    "GenericScheduler",
    "HTTPValidator",
    "CaptchaDetector",
    "SelectorProbe",
    "ContentExtractor",
    "StateDatabase",
    "EncryptedConfig",
    "BudgetManager",
    "AutomationRunner",
]