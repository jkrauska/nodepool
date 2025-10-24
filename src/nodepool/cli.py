"""Command-line interface for nodepool."""

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from nodepool.config_checker import ConfigChecker
from nodepool.database import AsyncDatabase
from nodepool.meshview_api import MeshViewAPIClient
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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
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

        # Save to database and import heard nodes
        console.print("\nSaving to database...")
        async with AsyncDatabase(db) as database:
            await database.initialize()

            total_heard = 0
            total_new = 0
            total_updated = 0

            for node in nodes:
                await database.save_node(node)

                # Import heard nodes from each discovered managed node
                if node.serial_port:
                    console.print(f"Importing heard nodes from {node.short_name}...")
                    try:
                        heard_nodes, heard_history = await manager.import_heard_nodes(
                            node.serial_port, node.id
                        )

                        # Track new vs updated nodes
                        new_nodes = []
                        updated_nodes = []

                        # Check which nodes are new
                        for heard_node in heard_nodes:
                            existing = await database.get_node(heard_node.id)
                            if existing is None:
                                new_nodes.append(heard_node)
                            else:
                                updated_nodes.append(heard_node)

                            # Save the node (insert or update)
                            await database.save_node(heard_node)

                        # Save heard history
                        for history in heard_history:
                            await database.save_heard_history(history)

                        total_heard += len(heard_nodes)
                        total_new += len(new_nodes)
                        total_updated += len(updated_nodes)

                        # Display results for this managed node
                        console.print(f"  [green]✓[/green] Imported {len(heard_nodes)} heard node(s)")
                        if new_nodes:
                            new_names = ", ".join(n.short_name for n in new_nodes[:5])
                            if len(new_nodes) > 5:
                                new_names += f", ... (+{len(new_nodes) - 5} more)"
                            console.print(f"    - {len(new_nodes)} new: {new_names}")
                        if updated_nodes:
                            console.print(f"    - {len(updated_nodes)} updated")

                    except Exception as e:
                        console.print(f"  [yellow]Warning: Could not import heard nodes: {e}[/yellow]")

        console.print(f"[green]Successfully saved {len(nodes)} managed node(s) to database.[/green]")
        if total_heard > 0:
            console.print(f"[green]Imported {total_heard} heard node(s) from the mesh.[/green]")
            if total_new > 0:
                console.print(f"  [cyan]→ {total_new} new node(s)[/cyan]")
            if total_updated > 0:
                console.print(f"  [dim]→ {total_updated} updated node(s)[/dim]")

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
@click.option(
    "--managed-only",
    is_flag=True,
    help="Show only managed nodes (default)",
)
@click.option(
    "--heard-only",
    is_flag=True,
    help="Show only heard nodes (from mesh)",
)
def list(db: str, show_all: bool, managed_only: bool, heard_only: bool):
    """List all nodes in the pool."""
    async def _list():
        async with AsyncDatabase(db) as database:
            await database.initialize()

            # Get nodes based on filter
            if heard_only:
                nodes = await database.get_heard_nodes()
            else:
                nodes = await database.get_all_nodes(active_only=not show_all)
                # Filter by managed status if requested
                if not heard_only and managed_only:
                    nodes = [n for n in nodes if n.managed]
                elif not managed_only and not heard_only:
                    # Default: show only managed nodes
                    nodes = [n for n in nodes if n.managed]

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            console.print("Run [bold]nodepool discover[/bold] to add nodes.")
            return

        # Determine table title
        if heard_only:
            title = "Heard Nodes (from Mesh)"
        elif managed_only or (not heard_only and not show_all):
            title = "Managed Nodes"
        else:
            title = "All Nodes"

        table = Table(title=title)
        table.add_column("Short Name", style="cyan", no_wrap=True)
        table.add_column("Node ID", style="magenta")
        table.add_column("Hardware", style="green")
        table.add_column("Firmware", style="blue")

        if heard_only:
            # Different columns for heard nodes
            table.add_column("SNR", style="yellow")
            table.add_column("Hops", style="blue")
        else:
            table.add_column("Serial Port", style="yellow")

        table.add_column("Status", style="white")

        for node in nodes:
            status = "✓ Active" if node.is_active else "✗ Inactive"
            status_style = "green" if node.is_active else "red"

            if heard_only:
                # Show SNR and hops for heard nodes
                snr_str = f"{node.snr:.1f}" if node.snr is not None else "?"
                hops_str = str(node.hops_away) if node.hops_away is not None else "?"

                table.add_row(
                    node.short_name,
                    node.id,
                    node.hw_model or "Unknown",
                    node.firmware_version or "Unknown",
                    snr_str,
                    hops_str,
                    f"[{status_style}]{status}[/{status_style}]",
                )
            else:
                # Show serial port for managed nodes
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
    "--port",
    help="Specific serial port to sync from",
)
def sync(db: str, port: str | None):
    """Sync heard nodes from connected managed node(s)."""
    async def _sync():
        async with AsyncDatabase(db) as database:
            await database.initialize()

            # Get managed nodes
            all_nodes = await database.get_all_nodes(active_only=True)
            managed_nodes = [n for n in all_nodes if n.managed and n.serial_port]

            if not managed_nodes:
                console.print("[yellow]No managed nodes with serial ports found.[/yellow]")
                console.print("Run [bold]nodepool discover[/bold] first.")
                return

            # Filter by port if specified
            if port:
                managed_nodes = [n for n in managed_nodes if n.serial_port == port]
                if not managed_nodes:
                    console.print(f"[red]No managed node found on port {port}[/red]")
                    return

            console.print(f"[bold blue]Syncing heard nodes from {len(managed_nodes)} managed node(s)...[/bold blue]\n")

            manager = NodeManager()
            total_heard = 0
            total_new = 0
            total_updated = 0

            for node in managed_nodes:
                console.print(f"Syncing from {node.short_name} ({node.serial_port})...")
                try:
                    heard_nodes, heard_history = await manager.import_heard_nodes(
                        node.serial_port, node.id
                    )

                    # Track new vs updated nodes
                    new_nodes = []
                    updated_nodes = []

                    # Check which nodes are new
                    for heard_node in heard_nodes:
                        existing = await database.get_node(heard_node.id)
                        if existing is None:
                            new_nodes.append(heard_node)
                        else:
                            updated_nodes.append(heard_node)

                        # Save the node (insert or update)
                        await database.save_node(heard_node)

                    # Save heard history
                    for history in heard_history:
                        await database.save_heard_history(history)

                    total_heard += len(heard_nodes)
                    total_new += len(new_nodes)
                    total_updated += len(updated_nodes)

                    # Display results for this managed node
                    console.print(f"  [green]✓[/green] Imported {len(heard_nodes)} heard node(s)")
                    if new_nodes:
                        new_names = ", ".join(n.short_name for n in new_nodes[:5])
                        if len(new_nodes) > 5:
                            new_names += f", ... (+{len(new_nodes) - 5} more)"
                        console.print(f"    - {len(new_nodes)} new: {new_names}")
                    if updated_nodes:
                        console.print(f"    - {len(updated_nodes)} updated")

                except Exception as e:
                    console.print(f"  [red]✗[/red] Error: {e}")

            # Summary
            console.print(f"\n[green]Successfully synced {total_heard} total heard node(s)[/green]")
            if total_new > 0:
                console.print(f"  [cyan]→ {total_new} new node(s)[/cyan]")
            if total_updated > 0:
                console.print(f"  [dim]→ {total_updated} updated node(s)[/dim]")

    run_async(_sync())


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
@click.option(
    "--seen-by",
    help="Filter by managed node that heard them",
)
def heard(db: str, seen_by: str | None):
    """List nodes heard on the mesh network."""
    async def _heard():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            nodes = await database.get_heard_nodes(seen_by=seen_by)

        if not nodes:
            console.print("[yellow]No heard nodes found.[/yellow]")
            console.print("Run [bold]nodepool sync[/bold] to import heard nodes.")
            return

        table = Table(title="Heard Nodes (from Mesh)")
        table.add_column("Short Name", style="cyan", no_wrap=True)
        table.add_column("Node ID", style="magenta")
        table.add_column("Hardware", style="green")
        table.add_column("SNR", style="yellow")
        table.add_column("Hops", style="blue")
        table.add_column("Last Seen", style="white")

        for node in nodes:
            snr_str = f"{node.snr:.1f}" if node.snr is not None else "?"
            hops_str = str(node.hops_away) if node.hops_away is not None else "?"

            table.add_row(
                node.short_name,
                node.id,
                node.hw_model or "Unknown",
                snr_str,
                hops_str,
                node.last_seen.strftime("%Y-%m-%d %H:%M"),
            )

        console.print(table)
        console.print(f"\nTotal: {len(nodes)} heard node(s)")

    run_async(_heard())


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


@cli.command()
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
@click.option(
    "--days-active",
    default=3,
    help="Number of days of activity to filter by",
    type=int,
)
@click.option(
    "--url",
    default="https://meshview.bayme.sh",
    help="MeshView API base URL",
)
def sync_meshview(db: str, days_active: int, url: str):
    """Sync nodes from MeshView API."""
    async def _sync_meshview():
        console.print(f"[bold blue]Fetching nodes from MeshView API...[/bold blue]")
        console.print(f"URL: {url}/api/nodes?days_active={days_active}\n")

        try:
            # Fetch nodes from API
            client = MeshViewAPIClient(base_url=url)
            with console.status("[bold green]Fetching data from API..."):
                nodes, heard_history = await client.fetch_nodes(days_active=days_active)

            if not nodes:
                console.print("[yellow]No nodes found from MeshView API.[/yellow]")
                return

            console.print(f"[green]Fetched {len(nodes)} node(s) from API[/green]\n")

            # Save to database
            console.print("Saving to database...")
            async with AsyncDatabase(db) as database:
                await database.initialize()

                # Track new vs updated nodes
                new_nodes = []
                updated_nodes = []

                for node in nodes:
                    existing = await database.get_node(node.id)
                    if existing is None:
                        new_nodes.append(node)
                    else:
                        updated_nodes.append(node)

                    # Save the node
                    await database.save_node(node)

                # Save heard history
                for history in heard_history:
                    await database.save_heard_history(history)

            # Display summary
            console.print(f"[green]Successfully synced {len(nodes)} node(s) from MeshView API[/green]")
            if new_nodes:
                console.print(f"  [cyan]→ {len(new_nodes)} new node(s)[/cyan]")
                # Show sample of new nodes
                sample = new_nodes[:5]
                for node in sample:
                    console.print(f"    - {node.short_name} ({node.id})")
                if len(new_nodes) > 5:
                    console.print(f"    ... and {len(new_nodes) - 5} more")
            if updated_nodes:
                console.print(f"  [dim]→ {len(updated_nodes)} updated node(s)[/dim]")

            console.print("\n[dim]Note: All nodes from MeshView are marked as heard from 'meshviewAPI'[/dim]")

        except Exception as e:
            console.print(f"[red]Error fetching from MeshView API: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")

    run_async(_sync_meshview())


if __name__ == "__main__":
    cli()