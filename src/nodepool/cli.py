"""Command-line interface for nodepool."""

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from nodepool.config_checker import ConfigChecker
from nodepool.database import AsyncDatabase
from nodepool.models import Node
from nodepool.node_manager import NodeManager

console = Console()


def run_async(coro):
    """Run an async coroutine from sync context.

    Args:
        coro: Coroutine to run

    Returns:
        Result of the coroutine
    """
    if sys.version_info >= (3, 11):
        # Python 3.11+ - use asyncio.Runner
        with asyncio.Runner() as runner:
            return runner.run(coro)
    else:
        # Fallback for older Python
        return asyncio.run(coro)


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Nodepool - Manage and maintain a group of Meshtastic nodes."""


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
@click.option(
    "--ports",
    multiple=True,
    help="Specific serial ports to scan (can be specified multiple times)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed output including failed port scans",
)
def discover(db: str, ports: tuple[str, ...], verbose: bool):
    """Discover Meshtastic nodes on serial ports and add them to the database."""
    async def _discover():
        console.print("[bold blue]Discovering Meshtastic nodes...[/bold blue]")

        manager = NodeManager()
        port_list = list(ports) if ports else None

        # Get list of ports to scan
        if port_list is None:
            port_list = await manager._list_serial_ports()

        if not port_list:
            console.print("[yellow]No serial ports found to scan.[/yellow]")
            return

        console.print(f"Scanning {len(port_list)} serial port(s)...\n")

        # Track discovered nodes
        discovered = []

        def progress_callback(port: str, result: Node | Exception):
            """Handle progress updates during discovery."""
            if isinstance(result, Node):
                discovered.append(result)
                console.print(
                    f"  [green]✓[/green] {port} → [bold]{result.short_name}[/bold] "
                    f"({result.hw_model})"
                )
            elif verbose:
                # Only show failures in verbose mode
                error_msg = str(result)
                # Shorten common error messages
                if "No node info" in error_msg or "Connection" in error_msg:
                    error_msg = "No response"
                console.print(f"  [dim]✗ {port} → {error_msg}[/dim]")

        # Discover nodes with progress callback
        nodes = await manager.discover_nodes(
            serial_ports=port_list,
            progress_callback=progress_callback
        )

        if not nodes:
            console.print("\n[yellow]No nodes discovered.[/yellow]")
            if not verbose:
                console.print(
                    "[dim]Tip: Use --verbose flag to see all scanned ports[/dim]"
                )
            return

        console.print(f"\n[green]Found {len(nodes)} node(s)![/green]")

        # Save to database
        console.print("\nSaving to database...")
        async with AsyncDatabase(db) as database:
            await database.initialize()
            for node in nodes:
                await database.save_node(node)

        console.print(f"[green]Successfully saved {len(nodes)} node(s) to database.[/green]")

    run_async(_discover())


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show inactive nodes as well",
)
def list(db: str, show_all: bool):
    """List all nodes in the pool."""
    async def _list():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            nodes = await database.get_all_nodes(active_only=not show_all)

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            console.print("Run [bold]nodepool discover[/bold] to add nodes.")
            return

        table = Table(title="Meshtastic Node Pool")
        table.add_column("Short Name", style="cyan", no_wrap=True)
        table.add_column("Node ID", style="magenta")
        table.add_column("Hardware", style="green")
        table.add_column("Firmware", style="blue")
        table.add_column("Serial Port", style="yellow")
        table.add_column("Status", style="white")

        for node in nodes:
            status = "✓ Active" if node.is_active else "✗ Inactive"
            status_style = "green" if node.is_active else "red"

            table.add_row(
                node.short_name,
                node.id,
                node.hw_model or "Unknown",
                node.firmware_version or "Unknown",
                node.serial_port or "Not set",
                f"[{status_style}]{status}[/{status_style}]",
            )

        console.print(table)
        console.print(f"\nTotal: {len(nodes)} node(s)")

    run_async(_list())


@cli.command()
@click.argument("node_id")
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
def info(node_id: str, db: str):
    """Show detailed information about a specific node."""
    async def _info():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            node = await database.get_node(node_id)

        if not node:
            console.print(f"[red]Node {node_id} not found in database.[/red]")
            return

        console.print(f"\n[bold cyan]Node Information: {node.short_name}[/bold cyan]")
        console.print(f"[dim]{'=' * 60}[/dim]")

        console.print("\n[bold]Basic Info:[/bold]")
        console.print(f"  ID: {node.id}")
        console.print(f"  Short Name: {node.short_name}")
        console.print(f"  Long Name: {node.long_name}")
        console.print(f"  Hardware: {node.hw_model or 'Unknown'}")
        console.print(f"  Firmware: {node.firmware_version or 'Unknown'}")
        console.print(f"  Serial Port: {node.serial_port or 'Not set'}")
        console.print(f"  Last Seen: {node.last_seen}")
        console.print(f"  Status: {'Active' if node.is_active else 'Inactive'}")

        if node.config:
            console.print("\n[bold]Configuration:[/bold]")

            if "lora" in node.config:
                lora = node.config["lora"]
                console.print("  LoRa:")
                console.print(f"    Hop Limit: {lora.get('hopLimit', 'Not set')}")
                console.print(f"    Region: {lora.get('region', 'Not set')}")

            if "device" in node.config:
                device = node.config["device"]
                console.print("  Device:")
                console.print(f"    Role: {device.get('role', 'Not set')}")

            if node.config.get("channels"):
                console.print("  Channels:")
                for channel in node.config["channels"]:
                    console.print(
                        f"    [{channel.get('index', '?')}] {channel.get('name', 'Unnamed')}"
                    )

        console.print()

    run_async(_info())


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
@click.option(
    "--ttl",
    default=7,
    help="Expected TTL/hop limit value",
    type=int,
)
@click.option(
    "--region",
    help="Expected LoRa region (e.g., US, EU_868)",
)
def check(db: str, ttl: int, region: str | None):
    """Run configuration checks on all nodes."""
    async def _check():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            nodes = await database.get_all_nodes()

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            return

        console.print(f"[bold blue]Running configuration checks on {len(nodes)} node(s)...[/bold blue]\n")

        checker = ConfigChecker(expected_ttl=ttl, expected_region=region)
        all_checks = await checker.check_all_nodes(nodes)

        # Save checks to database
        async with AsyncDatabase(db) as database:
            for check in all_checks:
                await database.save_config_check(check)

        # Display results by node
        for node in nodes:
            node_checks = [c for c in all_checks if c.node_id == node.id]

            console.print(f"[bold]{node.short_name}[/bold] ({node.id})")

            for check in node_checks:
                icon = ""
                style = ""
                match check.status:
                    case "pass":
                        icon = "✓"
                        style = "green"
                    case "fail":
                        icon = "✗"
                        style = "red"
                    case "warning":
                        icon = "⚠"
                        style = "yellow"

                console.print(f"  [{style}]{icon}[/{style}] {check.message}")

            console.print()

        # Summary
        pass_count = sum(1 for c in all_checks if c.status == "pass")
        fail_count = sum(1 for c in all_checks if c.status == "fail")
        warn_count = sum(1 for c in all_checks if c.status == "warning")

        console.print("[bold]Summary:[/bold]")
        console.print(f"  [green]✓ Passed: {pass_count}[/green]")
        console.print(f"  [red]✗ Failed: {fail_count}[/red]")
        console.print(f"  [yellow]⚠ Warnings: {warn_count}[/yellow]")

    run_async(_check())


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
def status(db: str):
    """Check reachability status of all nodes."""
    async def _status():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            nodes = await database.get_all_nodes()

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            return

        console.print(f"[bold blue]Checking status of {len(nodes)} node(s)...[/bold blue]\n")

        manager = NodeManager()
        with console.status("[bold green]Checking node reachability..."):
            statuses = await manager.check_all_reachability(nodes)

        table = Table(title="Node Status")
        table.add_column("Node", style="cyan", no_wrap=True)
        table.add_column("Serial Port", style="yellow")
        table.add_column("Status", style="white")
        table.add_column("Error", style="red")

        for status in statuses:
            reachable_text = "✓ Reachable" if status.reachable else "✗ Unreachable"
            reachable_style = "green" if status.reachable else "red"

            table.add_row(
                f"{status.node.short_name} ({status.node.id})",
                status.node.serial_port or "Not set",
                f"[{reachable_style}]{reachable_text}[/{reachable_style}]",
                status.error or "",
            )

        console.print(table)

        reachable_count = sum(1 for s in statuses if s.reachable)
        console.print(f"\n{reachable_count}/{len(statuses)} node(s) reachable")

    run_async(_status())


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
@click.option(
    "--output",
    "-o",
    help="Output file (default: stdout)",
    type=click.Path(),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "yaml"]),
    default="json",
    help="Output format",
)
def export(db: str, output: str | None, output_format: str):
    """Export node configurations."""
    async def _export():
        import json

        async with AsyncDatabase(db) as database:
            await database.initialize()
            nodes = await database.get_all_nodes(active_only=False)

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            return

        # Convert nodes to dict
        nodes_data = [
            {
                "id": node.id,
                "short_name": node.short_name,
                "long_name": node.long_name,
                "serial_port": node.serial_port,
                "hw_model": node.hw_model,
                "firmware_version": node.firmware_version,
                "last_seen": node.last_seen.isoformat(),
                "is_active": node.is_active,
                "config": node.config,
            }
            for node in nodes
        ]

        if output_format == "json":
            output_str = json.dumps(nodes_data, indent=2)
        else:  # yaml
            try:
                import yaml

                output_str = yaml.dump(nodes_data, default_flow_style=False)
            except ImportError:
                console.print("[red]YAML export requires PyYAML. Install with: uv pip install pyyaml[/red]")
                return

        if output:
            Path(output).write_text(output_str)
            console.print(f"[green]Exported {len(nodes)} node(s) to {output}[/green]")
        else:
            console.print(output_str)

    run_async(_export())


if __name__ == "__main__":
    cli()