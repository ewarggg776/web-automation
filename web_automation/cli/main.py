"""CLI command registry and main entry point."""

import click
import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any

from ..core.discovery import GenericMerger, GenericScheduler
from ..core.validators import HTTPValidator
from ..core.storage import StateDatabase, BudgetManager, EncryptedConfig
from ..core.runner import AutomationRunner, AutomationPipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class CommandRegistry:
    """Registry for CLI commands - allows adapters to register commands."""

    def __init__(self):
        self.commands: Dict[str, click.Command] = {}
        self.groups: Dict[str, click.Group] = {}

    def register(self, command: click.Command, group: str = None):
        """Register a command."""
        if group:
            if group not in self.groups:
                self.groups[group] = click.Group(name=group)
            self.groups[group].add_command(command)
        else:
            self.commands[command.name] = command

    def get_commands(self) -> list:
        """Get all registered commands."""
        cmds = list(self.commands.values())
        for group in self.groups.values():
            cmds.append(group)
        return cmds


# Global registry
registry = CommandRegistry()


# Core CLI commands
@click.group()
@click.option("--config", "-c", default="config/automation.yaml", help="Config file path")
@click.option("--db", "-d", default="data/state.db", help="Database path")
@click.option("--budget", "-b", default="data/budget.db", help="Budget database path")
@click.option("--dry-run/--live", default=True, help="Dry run mode (default)")
@click.option("--wallet", "-w", help="Wallet address for claims")
@click.option("--verbose", "-v", count=True, help="Increase verbosity")
@click.pass_context
def cli(ctx, config, db, budget, dry_run, wallet, verbose):
    """Web Automation Framework - Generic web automation for discovery and monitoring."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["db_path"] = db
    ctx.obj["budget_path"] = budget
    ctx.obj["dry_run"] = dry_run
    ctx.obj["wallet_address"] = wallet
    
    # Set log level based on verbosity
    if verbose >= 2:
        logging.getLogger().setLevel(logging.DEBUG)
    elif verbose >= 1:
        logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.WARNING)


@cli.command()
@click.pass_context
def discover(ctx):
    """Discover items from all configured sources."""
    click.echo("🔍 Starting discovery...")
    # Implementation delegated to adapter
    click.echo("Use adapter-specific discover command (e.g., 'faucet discover')")


@cli.command()
@click.option("--budget", "-b", type=float, help="Set budget amount")
@click.pass_context
def set_budget(ctx, budget):
    """Set or update budget."""
    if budget is None:
        click.echo("Current budget not set")
        return
    
    budget_mgr = BudgetManager(Path(ctx.obj["budget_path"]))
    budget_mgr.create_budget("default", budget)
    click.echo(f"💰 Budget set to ${budget:.2f}")


@cli.command()
@click.pass_context
def stats(ctx):
    """Show statistics."""
    db = StateDatabase(Path(ctx.obj["db_path"]))
    items = db.get_all_items()
    click.echo(f"📊 Total items: {len(items)}")
    
    if budget := ctx.obj.get("budget_path"):
        budget_mgr = BudgetManager(Path(budget))
        b = budget_mgr.get_budget("default")
        if b:
            spent = BudgetManager(Path(budget)).get_total_spent()
            remaining = BudgetManager(Path(budget)).get_remaining()
            click.echo(f"💰 Budget: ${b['initial_budget']:.2f} | Spent: ${spent:.2f} | Remaining: ${remaining:.2f}")


@cli.command()
@click.pass_context
def tui(ctx):
    """Launch TUI dashboard."""
    from ..tui.app import run_tui
    run_tui(
        config_path=ctx.obj["config_path"],
        db_path=ctx.obj["db_path"],
        budget_path=ctx.obj["budget_path"],
        dry_run=ctx.obj["dry_run"],
        wallet_address=ctx.obj["wallet_address"],
    )


def main():
    """Main entry point."""
    try:
        cli(obj={})
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()