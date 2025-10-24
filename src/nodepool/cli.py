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


@cli.group()
def connection():
    """Manage node connections."""


@connection.command("add")
@click.argument("connection_string")
@click.option(
    "--db",
    default="nodepool.db",
    help="Database file path",
    type=click.Path(),
)
def connection_add(connection_string: str, db: str):
    """Add a managed node connection (serial or TCP).
    
    CONNECTION_STRING can be:
    - Serial port: /dev/cu.usbmodem123, /dev/ttyUSB0, COM3
    - TCP: tcp://192.168.1.100:4403
    
    Examples:
    \b
      nodepool connection add /dev/cu.usbmodem123
      nodepool connection add tcp://192.168.1.100:4403
      nodepool connection add COM3
    """
    async def _connection_add():
        console.print(f"[bold blue]Connecting to node at {connection_string}...[/bold blue]")
        
        manager = NodeManager()
        
        try:
            # Connect to the node and get info
            with console.status("[bold green]Connecting..."):
                node = await manager.connect_to_node(connection_string)
            
            console.print(f"[green]✓[/green] Connected to [bold]{node.short_name}[/bold] ({node.long_name})")
            console.print(f"  Node ID: {node.id}")
            console.print(f"  Hardware: {node.hw_model}")
            console.print(f"  Firmware: {node.firmware_version}")
            
            # Save to database
            console.print("\nSaving to database...")
            async with AsyncDatabase(db) as database:
                await database.initialize()
                
                # Save node info
                await database.save_node(node)
                
                # Save connection (this makes it "managed")
                await database.save_connection(node.id, connection_string)
            
            console.print(f"[green]✓ Successfully added managed node connection[/green]")
            console.print("\n[dim]Tips:[/dim]")
            console.print("  - Run [bold]nodepool sync[/bold] to import heard nodes from this node")
            console.print("  - Run [bold]nodepool list[/bold] to see all connected nodes")
            
        except Exception as e:
            console.print(f"[red]✗ Failed to connect: {e}[/red]")
            import traceback
            if "--verbose" in sys.argv:
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
    
    run_async(_connection_add())


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
@click.option(
    "--network",
    is_flag=True,
    help="Also scan local network via mDNS for TCP nodes",
)
def discover(db: str, ports: tuple[str, ...], verbose: bool, network: bool):
    """Discover Meshtastic nodes on serial ports and optionally via mDNS on the local network."""
    async def _discover():
        console.print("[bold blue]Discovering Meshtastic nodes...[/bold blue]")

        manager = NodeManager()
        port_list = list(ports) if ports else None

        # Get list of ports to scan (skip if only doing network scan)
        if not network:
            if port_list is None:
                port_list = await manager._list_serial_ports()

            if not port_list:
                console.print("[yellow]No serial ports found to scan.[/yellow]")
                console.print("[dim]Tip: Use --network flag to scan local network via mDNS[/dim]")
                return

            if port_list:
                console.print(f"Scanning {len(port_list)} serial port(s)...\n")
        else:
            # Network-only mode, skip serial scanning
            port_list = []

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

        # Track which port each node was found on
        port_map = {}
        
        def progress_callback_with_tracking(port: str, result: Node | Exception):
            """Handle progress updates and track successful port-node mappings."""
            if isinstance(result, Node):
                port_map[result.id] = port
                discovered.append(result)
                # Check if this is a new or existing node
                is_new = result.id not in existing_node_ids
                status_text = "[cyan](new)[/cyan]" if is_new else "[dim](already known)[/dim]"
                console.print(
                    f"  [green]✓[/green] {port} → [bold]{result.short_name}[/bold] "
                    f"({result.hw_model}) {status_text}"
                )
            elif verbose:
                # Only show failures in verbose mode
                error_msg = str(result)
                # Shorten common error messages
                if "No node info" in error_msg or "Connection" in error_msg:
                    error_msg = "No response"
                console.print(f"  [dim]✗ {port} → {error_msg}[/dim]")

        # Track which nodes are new vs already known
        async with AsyncDatabase(db) as database:
            await database.initialize()
            existing_node_ids = {n.id for n in await database.get_all_nodes(active_only=False)}
        
        # Discover serial nodes with progress callback
        if port_list:
            nodes = await manager.discover_nodes(
                serial_ports=port_list,
                progress_callback=progress_callback_with_tracking
            )
        else:
            nodes = []

        # Discover mDNS nodes if --network flag is set
        if network:
            console.print("\nScanning local network via mDNS...\n")
            
            mdns_discovered = []
            
            def mdns_progress_callback(connection_string: str, instance_name: str):
                """Handle mDNS discovery progress."""
                console.print(f"  Found: {instance_name} at {connection_string}")
                mdns_discovered.append((connection_string, instance_name))
            
            # Discover via mDNS
            mdns_results = await manager.discover_mdns_nodes(
                timeout=5,
                progress_callback=mdns_progress_callback
            )
            
            # Try to connect to each discovered TCP node
            if mdns_results:
                console.print(f"\nConnecting to {len(mdns_results)} discovered node(s)...\n")
                
                for connection_string, instance_name in mdns_results:
                    try:
                        node = await manager.connect_to_node(connection_string)
                        nodes.append(node)
                        port_map[node.id] = connection_string
                        console.print(
                            f"  [green]✓[/green] {connection_string} → [bold]{node.short_name}[/bold] "
                            f"({node.hw_model})"
                        )
                    except Exception as e:
                        # Always show connection failures for mDNS discoveries
                        error_msg = str(e)
                        if len(error_msg) > 50:
                            error_msg = error_msg[:47] + "..."
                        console.print(f"  [red]✗[/red] {connection_string} → {error_msg}")

        if not nodes:
            console.print("\n[yellow]No nodes discovered.[/yellow]")
            if not verbose:
                console.print(
                    "[dim]Tip: Use --verbose flag to see all scanned ports[/dim]"
                )
            if not network:
                console.print(
                    "[dim]Tip: Use --network flag to scan local network via mDNS[/dim]"
                )
            return

        console.print(f"\n[green]Found {len(nodes)} node(s)![/green]")

        # Save to database with connections
        console.print("\nSaving to database...")
        async with AsyncDatabase(db) as database:
            await database.initialize()

            for node in nodes:
                # Save node data
                await database.save_node(node)
                # Save connection using the tracked port (this makes it "managed")
                if node.id in port_map:
                    await database.save_connection(node.id, port_map[node.id])

        console.print(f"[green]Successfully discovered and saved {len(nodes)} connected node(s).[/green]")
        console.print("[dim]Run [bold]nodepool sync[/bold] to import heard nodes from the mesh.[/dim]")

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
    "--connected-only",
    is_flag=True,
    help="Show only connected nodes (default)",
)
@click.option(
    "--heard-only",
    is_flag=True,
    help="Show only heard nodes (from mesh)",
)
def list(db: str, show_all: bool, connected_only: bool, heard_only: bool):
    """List all nodes in the pool."""
    async def _list():
        async with AsyncDatabase(db) as database:
            await database.initialize()

            # Get nodes based on filter
            if heard_only:
                nodes = await database.get_heard_nodes()
                node_ports = {}  # Heard nodes have no ports
            elif connected_only or (not heard_only):
                # Default: show only connected nodes
                connected = await database.get_connected_nodes()
                nodes = [n for n, _ in connected]
                node_ports = {n.id: p for n, p in connected}
            else:
                nodes = await database.get_all_nodes(active_only=not show_all)
                # Build port map for all nodes
                node_ports = {}
                for node in nodes:
                    port = await database.get_connection(node.id)
                    if port:
                        node_ports[node.id] = port

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            console.print("Run [bold]nodepool discover[/bold] to add nodes.")
            return

        # Determine table title
        if heard_only:
            title = "Heard Nodes (from Mesh)"
        elif connected_only or (not heard_only and not show_all):
            title = "Connected Nodes"
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
            table.add_column("Connection Method", style="yellow")

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
                # Show serial port for connected nodes
                serial_port = node_ports.get(node.id, "Not connected")
                table.add_row(
                    node.short_name,
                    node.id,
                    node.hw_model or "Unknown",
                    node.firmware_version or "Unknown",
                    serial_port,
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
    """Show detailed information about a specific node.
    
    NODE_ID can be specified with or without the ! prefix.
    Examples: 'abc123' or '!abc123'
    """
    # Normalize node_id - prepend ! if not present
    if not node_id.startswith("!"):
        node_id = f"!{node_id}"
    
    async def _info():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            node = await database.get_node(node_id)
            
            if not node:
                console.print(f"[red]Node {node_id} not found in database.[/red]")
                return
            
            serial_port = await database.get_connection(node_id)

        console.print(f"\n[bold cyan]Node Information: {node.short_name}[/bold cyan]")
        console.print(f"[dim]{'=' * 60}[/dim]")

        console.print("\n[bold]Basic Info:[/bold]")
        console.print(f"  ID: {node.id}")
        console.print(f"  Short Name: {node.short_name}")
        console.print(f"  Long Name: {node.long_name}")
        console.print(f"  Hardware: {node.hw_model or 'Unknown'}")
        console.print(f"  Firmware: {node.firmware_version or 'Unknown'}")
        console.print(f"  Connection: {serial_port or 'Not connected'}")
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

            if "security" in node.config:
                security = node.config["security"]
                console.print("  Security:")
                
                # Display admin keys (up to 3 slots)
                admin_keys = security.get('admin_keys', [])
                admin_keys_set = security.get('admin_keys_set', [])
                if admin_keys:
                    console.print(f"    Admin Keys: {len(admin_keys)} set")
                    for i, key in enumerate(admin_keys):
                        slot = admin_keys_set[i] if i < len(admin_keys_set) else i
                        console.print(f"      [{slot}] {key[:16]}... ({len(key)//2} bytes)")
                else:
                    console.print("    Admin Keys: None set")
                
                # Display PKI keys
                if security.get('public_key'):
                    pub_key = security['public_key']
                    console.print(f"    Public Key: {pub_key[:16]}... ({len(pub_key)//2} bytes)")
                if security.get('private_key'):
                    console.print(f"    Private Key: XXXXX--PRIVATE-KEY--XXXXX (hidden)")
                
                console.print(f"    Serial Enabled: {security.get('serial_enabled', 'Unknown')}")
                console.print(f"    Admin Channel Enabled: {security.get('admin_channel_enabled', False)}")
                console.print(f"    Managed: {security.get('is_managed', False)}")

            if node.config.get("channels"):
                console.print("  Channels:")
                for channel in node.config["channels"]:
                    psk = channel.get('psk')
                    psk_info = f" [PSK: {psk[:8]}...]" if psk else " [Not encrypted]"
                    console.print(
                        f"    [{channel.get('index', '?')}] {channel.get('name', 'Unnamed')}{psk_info}"
                    )
            
            # Position config
            if "position" in node.config:
                pos = node.config["position"]
                console.print("  Position:")
                if pos.get("position_broadcast_secs"):
                    interval_min = pos["position_broadcast_secs"] // 60
                    console.print(f"    Broadcast: {pos['position_broadcast_secs']}s ({interval_min} min)")
                console.print(f"    Smart Mode: {pos.get('position_broadcast_smart_enabled', False)}")
                console.print(f"    GPS Enabled: {pos.get('gps_enabled', True)}")
                console.print(f"    Fixed Position: {pos.get('fixed_position', False)}")
            
            # Module configs (only show enabled or configured modules)
            console.print("\n[bold]Modules:[/bold]")
            
            # MQTT
            if "mqtt" in node.config:
                mqtt = node.config["mqtt"]
                if mqtt.get("enabled"):
                    console.print("  [cyan]MQTT:[/cyan]")
                    console.print(f"    Enabled: {mqtt['enabled']}")
                    if mqtt.get("address"):
                        console.print(f"    Address: {mqtt['address']}")
                    console.print(f"    Map Reporting: {mqtt.get('map_reporting_enabled', False)} {'[green](OK to MQTT)[/green]' if mqtt.get('map_reporting_enabled') else '[dim](IGNORE MQTT)[/dim]'}")
                    console.print(f"    JSON: {mqtt.get('json_enabled', False)}")
                    console.print(f"    TLS: {mqtt.get('tls_enabled', False)}")
            
            # Telemetry
            if "telemetry" in node.config:
                telem = node.config["telemetry"]
                if telem.get("device_update_interval") or telem.get("environment_measurement_enabled"):
                    console.print("  [cyan]Telemetry:[/cyan]")
                    if telem.get("device_update_interval"):
                        console.print(f"    Device Interval: {telem['device_update_interval']}s")
                    if telem.get("environment_update_interval"):
                        console.print(f"    Environment Interval: {telem['environment_update_interval']}s")
                    console.print(f"    Environment: {telem.get('environment_measurement_enabled', False)}")
                    console.print(f"    Display °F: {telem.get('environment_display_fahrenheit', False)}")
            
            # Store & Forward
            if "store_forward" in node.config:
                sf = node.config["store_forward"]
                if sf.get("enabled"):
                    console.print("  [cyan]Store & Forward:[/cyan]")
                    console.print(f"    Enabled: {sf['enabled']}")
                    console.print(f"    Records: {sf.get('records', 0)}")
                    console.print(f"    Heartbeat: {sf.get('heartbeat', False)}")
            
            # Range Test
            if "range_test" in node.config:
                rt = node.config["range_test"]
                if rt.get("enabled"):
                    console.print("  [cyan]Range Test:[/cyan]")
                    console.print(f"    Enabled: {rt['enabled']}")
                    console.print(f"    Sender: {rt.get('sender', 0)}")
                    console.print(f"    Save: {rt.get('save', False)}")
            
            # External Notification
            if "external_notification" in node.config:
                en = node.config["external_notification"]
                if en.get("enabled"):
                    console.print("  [cyan]External Notification:[/cyan]")
                    console.print(f"    Enabled: {en['enabled']}")
                    console.print(f"    Alert on Message: {en.get('alert_message', False)}")
                    console.print(f"    Alert on Bell: {en.get('alert_bell', False)}")
            
            # Serial Module
            if "serial_module" in node.config:
                ser = node.config["serial_module"]
                if ser.get("enabled"):
                    console.print("  [cyan]Serial Module:[/cyan]")
                    console.print(f"    Enabled: {ser['enabled']}")
                    console.print(f"    Baud: {ser.get('baud', 0)}")
                    console.print(f"    Echo: {ser.get('echo', False)}")
            
            # Neighbor Info
            if "neighbor_info" in node.config:
                ni = node.config["neighbor_info"]
                if ni.get("enabled"):
                    console.print("  [cyan]Neighbor Info:[/cyan]")
                    console.print(f"    Enabled: {ni['enabled']}")
                    console.print(f"    Update Interval: {ni.get('update_interval', 0)}s")
            
            # Detection Sensor
            if "detection_sensor" in node.config:
                ds = node.config["detection_sensor"]
                if ds.get("enabled"):
                    console.print("  [cyan]Detection Sensor:[/cyan]")
                    console.print(f"    Enabled: {ds['enabled']}")
                    console.print(f"    Monitor Pin: {ds.get('monitor_pin', 0)}")
            
            # Paxcounter
            if "paxcounter" in node.config:
                pc = node.config["paxcounter"]
                if pc.get("enabled"):
                    console.print("  [cyan]Paxcounter:[/cyan]")
                    console.print(f"    Enabled: {pc['enabled']}")
                    console.print(f"    Update Interval: {pc.get('paxcounter_update_interval', 0)}s")

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
    """Run configuration checks on managed nodes."""
    async def _check():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            # Only check managed nodes (those with connections)
            connected_nodes = await database.get_connected_nodes()
            nodes = [n for n, _ in connected_nodes]

        if not nodes:
            console.print("[yellow]No managed nodes found in database.[/yellow]")
            console.print("Run [bold]nodepool discover[/bold] to add managed nodes.")
            return

        console.print(f"[bold blue]Running configuration checks on {len(nodes)} managed node(s)...[/bold blue]\n")

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
    """Check reachability status of connected nodes."""
    async def _status():
        async with AsyncDatabase(db) as database:
            await database.initialize()
            connected_nodes = await database.get_connected_nodes()

        if not connected_nodes:
            console.print("[yellow]No connected nodes found.[/yellow]")
            console.print("Run [bold]nodepool discover[/bold] first.")
            return

        console.print(f"[bold blue]Checking status of {len(connected_nodes)} connected node(s)...[/bold blue]\n")

        manager = NodeManager()
        statuses = []
        
        with console.status("[bold green]Checking node reachability..."):
            for node, serial_port in connected_nodes:
                status = await manager.check_node_reachability(node, serial_port)
                statuses.append((status, serial_port))

        table = Table(title="Node Status")
        table.add_column("Node", style="cyan", no_wrap=True)
        table.add_column("Connection Method", style="yellow")
        table.add_column("Status", style="white")
        table.add_column("Error", style="red")

        for status, connection_string in statuses:
            reachable_text = "✓ Reachable" if status.reachable else "✗ Unreachable"
            reachable_style = "green" if status.reachable else "red"

            table.add_row(
                f"{status.node.short_name} ({status.node.id})",
                connection_string,
                f"[{reachable_style}]{reachable_text}[/{reachable_style}]",
                status.error or "",
            )

        console.print(table)

        reachable_count = sum(1 for s, _ in statuses if s.reachable)
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

            # Get connected nodes (managed via connections table)
            connected_nodes = await database.get_connected_nodes()

            if not connected_nodes:
                console.print("[yellow]No connected nodes found.[/yellow]")
                console.print("Run [bold]nodepool discover[/bold] first.")
                return

            # Filter by port if specified
            if port:
                connected_nodes = [(n, p) for n, p in connected_nodes if p == port]
                if not connected_nodes:
                    console.print(f"[red]No connected node found on port {port}[/red]")
                    return

            console.print(f"[bold blue]Syncing heard nodes from {len(connected_nodes)} connected node(s)...[/bold blue]\n")

            manager = NodeManager()
            total_heard = 0
            total_new = 0
            total_updated = 0

            for node, serial_port in connected_nodes:
                console.print(f"Syncing from {node.short_name} ({serial_port})...")
                try:
                    # First, refresh the managed node's config
                    refreshed_node = await manager.connect_to_node(serial_port)
                    await database.save_node(refreshed_node)
                    console.print(f"  [dim]→ Refreshed config for {node.short_name}[/dim]")
                    
                    # Then import heard nodes
                    heard_nodes, heard_history = await manager.import_heard_nodes(
                        serial_port, node.id
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
        table.add_column("Long Name", style="white")
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
                node.long_name,
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
            
            # Build export data with connection info
            nodes_data = []
            for node in nodes:
                connection_string = await database.get_connection(node.id)
                nodes_data.append({
                    "id": node.id,
                    "short_name": node.short_name,
                    "long_name": node.long_name,
                    "connection_string": connection_string,
                    "hw_model": node.hw_model,
                    "firmware_version": node.firmware_version,
                    "last_seen": node.last_seen.isoformat(),
                    "is_active": node.is_active,
                    "config": node.config,
                })

        if not nodes:
            console.print("[yellow]No nodes found in database.[/yellow]")
            return

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