# web-automation

> Generic web automation framework for discovery, validation, and monitoring — with a pluggable adapter system for any domain.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/web-automation.svg)](https://pypi.org/project/web-automation/)

## Overview

`web-automation` is a **domain-agnostic** framework for building web automation pipelines. It handles the hard parts — discovery, validation, deduplication, scheduling, state persistence, and CLI/TUI interfaces — so you only write the domain-specific logic.

Originally built for **testnet faucet claiming**, it's now a reusable foundation for:
- 🔍 **Job board aggregators** (Indeed, LinkedIn, Greenhouse)
- 🛒 **E-commerce monitors** (price drops, stock alerts)
- 🏠 **Real estate watchers** (new listings, price changes)
- 🎫 **Appointment watchers** (DMV, visa, camping)
- 🛡️ **Security scanners** (CVE feeds, exposed credentials)
- 🪙 **Crypto faucet claimers** (testnet/mainnet)

## Features

| Feature | Description |
|---------|-------------|
| **Multi-source discovery** | Aggregate from APIs, HTML scraping, RSS, GraphQL |
| **Smart validation** | HTTP health, CAPTCHA detection, selector probing, content extraction |
| **Deduplication & scoring** | URL normalization, metadata merging, configurable ranking |
| **Scheduling & cooldowns** | Per-item cooldowns, rate limiting, periodic tasks |
| **State persistence** | SQLite-backed registry with full history |
| **Budget management** | Per-campaign spend limits with expense tracking |
| **Encrypted config** | Fernet-encrypted YAML for API keys/secrets |
| **Dry-run / Live modes** | Test safely before spending real gas/money |
| **CLI + TUI** | Full terminal dashboard with live updates |
| **Adapter pattern** | Add new domains in <100 lines |

## Quick Start

```bash
# Install
pip install web-automation

# Or from source
git clone https://github.com/ewarggg776/web-automation
cd web-automation
pip install -e .

# Configure (encrypted)
web-auto config set api_key "your-key"

# Run discovery
web-auto discover --chain ethereum-sepolia

# Run claims (dry-run first)
web-auto claim --dry-run
web-auto claim --live

# Launch dashboard
web-auto tui
```

## Architecture

```
web_automation/
├── core/                    # Domain-agnostic framework
│   ├── discovery/           # Models, Merger, Scheduler
│   ├── validators/          # HTTP, CAPTCHA, Selectors, Content
│   ├── storage/             # StateDB, BudgetManager, EncryptedConfig
│   └── runner.py            # AutomationRunner, Pipeline
├── cli/                     # Click CLI + adapter registry
├── tui/                     # Textual dashboard (live stats, logs)
├── adapters/                # Domain-specific implementations
│   ├── faucet/              # Testnet/mainnet faucets
│   ├── jobs/                # Job board aggregator
│   ├── ecommerce/           # Price/stock monitor
│   └── appointments/        # Slot watcher
└── tests/                   # Unit + integration tests
```

## Creating an Adapter

```python
# adapters/my_domain/__init__.py
from web_automation.core.discovery import AbstractSource, DiscoveredItem

class MySource(AbstractSource):
    async def discover(self) -> list[DiscoveredItem]:
        # Your scraping/API logic here
        return [DiscoveredItem(name="...", url="...", source="my_source")]
    
    async def validate(self, item: DiscoveredItem) -> bool:
        # Your validation logic
        return True

# Register in CLI
from web_automation.cli.main import registry
from .sources import MySource
registry.register(MySource())
```

Then use it:
```bash
web-auto discover --source my_source
web-auto claim --source my_source
```

## Configuration

```yaml
# config/automation.yaml (encrypted at rest)
sources:
  - name: my_api
    url: https://api.example.com
    rate_limit: 2.0
    selectors:
      item: ".listing"
      title: "h3"
      price: ".price"

validators:
  http_check: true
  captcha_detect: true
  selector_probe: true

scheduler:
  max_concurrent: 3
  default_cooldown_hours: 24

budget:
  default: 50.0  # USD
  period_days: 30
```

## Requirements

- Python 3.10+
- Playwright (for browser-based validation): `playwright install chromium`
- Optional: `web3.py`, `eth-account` for crypto adapters

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check .

# Type check
mypy web_automation/
```

## License

MIT License — see [LICENSE](LICENSE) for details.

## Related

- [Playwright](https://playwright.dev/) — Browser automation
- [Textual](https://textual.textualize.io/) — Terminal UI framework
- [Click](https://click.palletsprojects.com/) — CLI framework