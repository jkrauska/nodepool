"""Node manager for Meshtastic operations."""

import asyncio
import logging
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nodepool.models import Node, NodeStatus

logger = logging.getLogger(__name__)


class NodeManager:
    """Manages Meshtastic node operations."""

    def __init__(self):
        """Initialize node manager."""
        self.discovered_nodes: dict[str, Node] = {}

    async def discover_nodes(
        self,
        serial_ports: list[str] | None = None,
        progress_callback: Callable[[str, Node | Exception], None] | None = None,
    ) -> list[Node]:
        """Discover Meshtastic nodes on serial ports.

        Args:
            serial_ports: List of serial ports to scan. If None, scans common ports.
            progress_callback: Optional callback function(port, result) called after each port scan.

        Returns:
            List of discovered Node objects
        """
        if serial_ports is None:
            serial_ports = await self._list_serial_ports()

        # Use asyncio.gather to scan ports concurrently
        tasks = [self._scan_port(port) for port in serial_ports]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        nodes = []
        for port, result in zip(serial_ports, results):
            if isinstance(result, Node):
                nodes.append(result)
                self.discovered_nodes[result.id] = result
                if progress_callback:
                    progress_callback(port, result)
            elif isinstance(result, Exception):
                logger.debug(f"Port scan failed on {port}: {result}")
                if progress_callback:
                    progress_callback(port, result)

        return nodes

    async def _list_serial_ports(self) -> list[str]:
        """List available serial ports based on the operating system.

        Returns:
            List of serial port paths appropriate for the current OS
        """
        ports = []
        system = platform.system()

        if system == "Darwin":  # macOS
            # macOS USB serial device patterns - only use cu.* (callout devices)
            # Note: tty.* devices cause "Resource busy" conflicts when cu.* is in use
            patterns = [
                "cu.usbmodem*",    # Most common for Meshtastic devices
                "cu.usbserial*",   # FTDI and similar USB-to-serial adapters
            ]
            for pattern in patterns:
                ports.extend(str(p) for p in Path("/dev").glob(pattern))

        elif system == "Linux":
            # Linux USB serial device patterns
            patterns = [
                "ttyUSB*",  # USB-to-serial adapters
                "ttyACM*",  # USB CDC/ACM devices (Arduino, etc.)
            ]
            for pattern in patterns:
                ports.extend(str(p) for p in Path("/dev").glob(pattern))

        elif system == "Windows":
            # Windows COM ports - scan COM1 through COM20
            ports = [f"COM{i}" for i in range(1, 21)]

        else:
            # Unknown OS - log warning and try common patterns
            logger.warning(f"Unknown operating system: {system}, scanning common patterns")
            # Try all common patterns as fallback
            for pattern in ["cu.usbmodem*", "cu.usbserial*", "tty.usb*", "ttyUSB*", "ttyACM*"]:
                if Path("/dev").exists():
                    ports.extend(str(p) for p in Path("/dev").glob(pattern))
            # Also try Windows COM ports
            ports.extend(f"COM{i}" for i in range(1, 21))

        return ports

    async def _scan_port(self, port: str) -> Node:
        """Scan a single serial port for a Meshtastic node.

        Args:
            port: Serial port path to scan

        Returns:
            Node object if found

        Raises:
            Exception: If no node found or connection fails
        """
        # Run blocking serial operations in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._scan_port_blocking, port)

    def _scan_port_blocking(self, port: str) -> Node:
        """Blocking serial port scan (runs in thread pool).

        Args:
            port: Serial port path to scan

        Returns:
            Node object if found

        Raises:
            Exception: If no node found or connection fails
        """
        try:
            import meshtastic.serial_interface

            # Connect with protocol enabled to properly query node information
            interface = meshtastic.serial_interface.SerialInterface(port)

            # Get node info from myInfo (protobuf object)
            my_info = interface.myInfo
            if not my_info:
                interface.close()
                raise ValueError(f"No node info available on {port}")

            # Get my node number and convert to hex ID format for lookup
            my_node_num = my_info.my_node_num
            # Node IDs in the nodes dict are formatted as !xxxxxxxx (hex)
            node_id = f"!{my_node_num:08x}"
            
            # Look up node details in the nodes dictionary
            if node_id not in interface.nodes:
                interface.close()
                raise ValueError(f"Node {node_id} (num: {my_node_num}) not found in nodes dict")
            
            node_data = interface.nodes[node_id]
            
            # Extract node details from user info
            user = node_data.get("user", {})
            node_id = user.get("id", "unknown")
            short_name = user.get("shortName", "UNKNOWN")
            long_name = user.get("longName", "Unknown Node")
            hw_model = user.get("hwModel", "UNKNOWN")

            # Get firmware version from metadata
            firmware_version = None
            if hasattr(interface, "metadata") and hasattr(interface.metadata, "firmware_version"):
                firmware_version = interface.metadata.firmware_version
            if not firmware_version:
                # Fallback to pio_env if metadata not available
                firmware_version = my_info.pio_env

            # Get configuration
            config = self._extract_config(interface)

            interface.close()

            return Node(
                id=node_id,
                short_name=short_name,
                long_name=long_name,
                serial_port=port,
                hw_model=hw_model,
                firmware_version=firmware_version,
                last_seen=datetime.now(),
                is_active=True,
                config=config,
            )

        except Exception as e:
            logger.debug(f"Failed to scan port {port}: {e}")
            raise

    def _extract_config(self, interface: Any) -> dict[str, Any]:
        """Extract configuration from Meshtastic interface.

        Args:
            interface: Meshtastic interface object

        Returns:
            Configuration dictionary
        """
        config: dict[str, Any] = {}

        try:
            if hasattr(interface, "localNode"):
                local_node = interface.localNode

                # Extract LoRa config
                if hasattr(local_node, "radioConfig"):
                    radio_config = local_node.radioConfig
                    config["lora"] = {
                        "hopLimit": getattr(radio_config, "hopLimit", None),
                        "region": getattr(radio_config, "region", None),
                    }

                # Extract device config
                if hasattr(local_node, "deviceConfig"):
                    device_config = local_node.deviceConfig
                    config["device"] = {
                        "role": getattr(device_config, "role", None),
                    }

                # Extract channels
                if hasattr(local_node, "channels"):
                    config["channels"] = []
                    for channel in local_node.channels:
                        config["channels"].append(
                            {
                                "name": getattr(channel, "name", ""),
                                "index": getattr(channel, "index", 0),
                            }
                        )

        except Exception as e:
            logger.warning(f"Failed to extract full config: {e}")

        return config

    async def check_node_reachability(self, node: Node) -> NodeStatus:
        """Check if a node is reachable.

        Args:
            node: Node to check

        Returns:
            NodeStatus object with reachability info
        """
        if not node.serial_port:
            return NodeStatus(
                node=node,
                reachable=False,
                last_check=datetime.now(),
                error="No serial port configured",
            )

        try:
            # Try to connect briefly
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._check_port_reachable, node.serial_port
            )

            return NodeStatus(
                node=node,
                reachable=True,
                last_check=datetime.now(),
                error=None,
            )

        except Exception as e:
            return NodeStatus(
                node=node,
                reachable=False,
                last_check=datetime.now(),
                error=str(e),
            )

    def _check_port_reachable(self, port: str) -> None:
        """Check if a serial port is reachable (blocking).

        Args:
            port: Serial port to check

        Raises:
            Exception: If port is not reachable
        """
        import meshtastic.serial_interface

        # Connect with protocol enabled to verify node responds
        interface = meshtastic.serial_interface.SerialInterface(port)
        if not interface.myInfo:
            raise ValueError("No response from node")
        interface.close()

    async def check_all_reachability(self, nodes: list[Node]) -> list[NodeStatus]:
        """Check reachability of all nodes concurrently.

        Args:
            nodes: List of nodes to check

        Returns:
            List of NodeStatus objects
        """
        tasks = [self.check_node_reachability(node) for node in nodes]
        return await asyncio.gather(*tasks)