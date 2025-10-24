"""Node manager for Meshtastic operations."""

import asyncio
import logging
import platform
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from nodepool.models import HeardHistory, Node, NodeStatus

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

    async def discover_mdns_nodes(
        self,
        timeout: int = 5,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> list[tuple[str, str]]:
        """Discover Meshtastic nodes via mDNS/Zeroconf.

        Args:
            timeout: Seconds to wait for mDNS responses
            progress_callback: Optional callback(connection_string, instance_name)

        Returns:
            List of tuples (connection_string, instance_name)
            e.g., [("tcp://192.168.1.100:4403", "Meshtastic-2")]
        """
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

        discovered: list[tuple[str, str]] = []

        class MeshtasticListener(ServiceListener):
            def __init__(self):
                self.services: dict[str, Any] = {}

            def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                info = zc.get_service_info(type_, name)
                if info:
                    self.services[name] = info
                    # Extract addresses and create connection string
                    if info.parsed_addresses():
                        addr = info.parsed_addresses()[0]
                        port = info.port
                        connection_string = f"tcp://{addr}:{port}"
                        instance_name = name.replace(f".{type_}", "")
                        discovered.append((connection_string, instance_name))
                        if progress_callback:
                            progress_callback(connection_string, instance_name)

            def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                pass

            def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                pass

        # Run mDNS discovery in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._discover_mdns_blocking, timeout, MeshtasticListener()
        )

        return discovered

    def _discover_mdns_blocking(
        self, timeout: int, listener: Any
    ) -> None:
        """Blocking mDNS discovery (runs in thread pool).

        Args:
            timeout: Seconds to wait for responses
            listener: ServiceListener instance
        """
        from zeroconf import ServiceBrowser, Zeroconf
        import time

        zeroconf = Zeroconf()
        try:
            # Browse for Meshtastic services
            ServiceBrowser(zeroconf, "_meshtastic._tcp.local.", listener)
            # Wait for responses
            time.sleep(timeout)
        finally:
            zeroconf.close()

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

    async def connect_to_node(self, connection_string: str) -> Node:
        """Connect to a node via serial or TCP and return node info.

        Args:
            connection_string: Connection string (e.g., /dev/cu.usbmodem123, tcp://192.168.1.100:4403)

        Returns:
            Node object with connection info

        Raises:
            Exception: If connection fails or no node info available
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._connect_to_node_blocking, connection_string)

    def _connect_to_node_blocking(self, connection_string: str) -> Node:
        """Blocking connection to node (runs in thread pool).

        Args:
            connection_string: Connection string (serial port or tcp://host:port)

        Returns:
            Node object

        Raises:
            Exception: If connection fails
        """
        try:
            # Determine connection type and create appropriate interface
            if connection_string.startswith("tcp://"):
                import meshtastic.tcp_interface
                # Extract host:port from tcp://host:port
                host_port = connection_string[6:]  # Remove "tcp://"
                interface = meshtastic.tcp_interface.TCPInterface(hostname=host_port)
            else:
                import meshtastic.serial_interface
                interface = meshtastic.serial_interface.SerialInterface(connection_string)

            # Get node info
            my_info = interface.myInfo
            if not my_info:
                interface.close()
                raise ValueError(f"No node info available on {connection_string}")

            # Get my node number and convert to hex ID format for lookup
            my_node_num = my_info.my_node_num
            node_id = f"!{my_node_num:08x}"

            # Look up node details in the nodes dictionary
            if node_id not in interface.nodes:
                interface.close()
                raise ValueError(f"Node {node_id} not found in nodes dict")

            node_data = interface.nodes[node_id]

            # Extract node details
            user = node_data.get("user", {})
            node_id = user.get("id", "unknown")
            short_name = user.get("shortName", "UNKNOWN")
            long_name = user.get("longName", "Unknown Node")
            
            # Try to get hardware model from myInfo first (more reliable), then fall back to user dict
            hw_model = None
            if hasattr(my_info, 'hw_model_string'):
                hw_model = my_info.hw_model_string
            elif hasattr(my_info, 'hw_model'):
                # hw_model is an enum, try to convert to string
                try:
                    from meshtastic import hardware
                    hw_model = hardware.Models(my_info.hw_model).name
                except (ImportError, ValueError, AttributeError):
                    pass
            
            # Fallback to user dict if we couldn't get it from myInfo
            if not hw_model:
                hw_model = user.get("hwModel", "UNKNOWN")

            # Get firmware version
            firmware_version = None
            if hasattr(interface, "metadata") and hasattr(interface.metadata, "firmware_version"):
                firmware_version = interface.metadata.firmware_version
            if not firmware_version:
                firmware_version = my_info.pio_env

            # Get configuration
            config = self._extract_config(interface)

            interface.close()

            return Node(
                id=node_id,
                short_name=short_name,
                long_name=long_name,
                hw_model=hw_model,
                firmware_version=firmware_version,
                last_seen=datetime.now(),
                is_active=True,
                config=config,
            )

        except Exception as e:
            logger.debug(f"Failed to connect to {connection_string}: {e}")
            raise

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
            if hasattr(interface, "localNode") and interface.localNode:
                local_node = interface.localNode

                # Modern API: Use localConfig
                if hasattr(local_node, "localConfig"):
                    local_config = local_node.localConfig
                    
                    # Extract LoRa config
                    if hasattr(local_config, "lora"):
                        lora = local_config.lora
                        config["lora"] = {
                            "hopLimit": getattr(lora, "hop_limit", None),
                            "region": getattr(lora, "region", None),
                        }
                    
                    # Extract device config
                    if hasattr(local_config, "device"):
                        device = local_config.device
                        config["device"] = {
                            "role": getattr(device, "role", None),
                        }
                
                # Legacy API fallback: Try radioConfig
                elif hasattr(local_node, "radioConfig"):
                    radio_config = local_node.radioConfig
                    config["lora"] = {
                        "hopLimit": getattr(radio_config, "hopLimit", None),
                        "region": getattr(radio_config, "region", None),
                    }
                    
                    if hasattr(local_node, "deviceConfig"):
                        device_config = local_node.deviceConfig
                        config["device"] = {
                            "role": getattr(device_config, "role", None),
                        }

                # Extract security config (modern API)
                if hasattr(local_config, "security"):
                    security = local_config.security
                    # Store admin_key as hex string (it's a bytes field)
                    admin_key_bytes = getattr(security, "admin_key", b"")
                    public_key_bytes = getattr(security, "public_key", b"")
                    
                    # Convert to hex, handling both bytes and RepeatedScalarContainer
                    admin_key_hex = None
                    if admin_key_bytes:
                        if isinstance(admin_key_bytes, bytes):
                            admin_key_hex = admin_key_bytes.hex()
                        else:
                            # Handle RepeatedScalarContainer or other types
                            try:
                                admin_key_hex = bytes(admin_key_bytes).hex()
                            except (TypeError, ValueError):
                                pass
                    
                    public_key_hex = None
                    if public_key_bytes:
                        if isinstance(public_key_bytes, bytes):
                            public_key_hex = public_key_bytes.hex()
                        else:
                            try:
                                public_key_hex = bytes(public_key_bytes).hex()
                            except (TypeError, ValueError):
                                pass
                    
                    config["security"] = {
                        "admin_key": admin_key_hex,
                        "admin_key_set": bool(admin_key_bytes),
                        "public_key": public_key_hex,
                        "public_key_set": bool(public_key_bytes),
                        "serial_enabled": getattr(security, "serial_enabled", True),
                        "admin_channel_index": getattr(security, "admin_channel_index", 0),
                    }
                
                # Extract channels with encryption info (same for both APIs)
                if hasattr(local_node, "channels"):
                    config["channels"] = []
                    for channel in local_node.channels:
                        psk_bytes = getattr(channel, "psk", b"")
                        
                        # Convert PSK to hex, handling both bytes and RepeatedScalarContainer
                        psk_hex = None
                        if psk_bytes:
                            if isinstance(psk_bytes, bytes):
                                psk_hex = psk_bytes.hex()
                            else:
                                # Handle RepeatedScalarContainer or other types
                                try:
                                    psk_hex = bytes(psk_bytes).hex()
                                except (TypeError, ValueError):
                                    pass
                        
                        config["channels"].append(
                            {
                                "name": getattr(channel, "name", ""),
                                "index": getattr(channel, "index", 0),
                                "psk": psk_hex,
                                "psk_set": bool(psk_bytes),
                                "uplink_enabled": getattr(channel, "uplink_enabled", False),
                                "downlink_enabled": getattr(channel, "downlink_enabled", False),
                            }
                        )
                
                # Extract position config (in localConfig, not moduleConfig)
                if hasattr(local_config, "position"):
                    position = local_config.position
                    config["position"] = {
                        "position_broadcast_secs": getattr(position, "position_broadcast_secs", 0),
                        "position_broadcast_smart_enabled": getattr(position, "position_broadcast_smart_enabled", False),
                        "fixed_position": getattr(position, "fixed_position", False),
                        "gps_enabled": getattr(position, "gps_enabled", True),
                        "gps_update_interval": getattr(position, "gps_update_interval", 0),
                        "gps_attempt_time": getattr(position, "gps_attempt_time", 0),
                        "position_flags": getattr(position, "position_flags", 0),
                    }
                
                # Extract module configs (modern API)
                if hasattr(local_node, "moduleConfig"):
                    module_config = local_node.moduleConfig
                    
                    # MQTT Module
                    if hasattr(module_config, "mqtt"):
                        mqtt = module_config.mqtt
                        config["mqtt"] = {
                            "enabled": getattr(mqtt, "enabled", False),
                            "address": getattr(mqtt, "address", ""),
                            "username": getattr(mqtt, "username", ""),
                            "password": getattr(mqtt, "password", ""),
                            "encryption_enabled": getattr(mqtt, "encryption_enabled", False),
                            "json_enabled": getattr(mqtt, "json_enabled", False),
                            "tls_enabled": getattr(mqtt, "tls_enabled", False),
                            "root": getattr(mqtt, "root", ""),
                            "proxy_to_client_enabled": getattr(mqtt, "proxy_to_client_enabled", False),
                            "map_reporting_enabled": getattr(mqtt, "map_reporting_enabled", False),
                        }
                    
                    # Serial Module
                    if hasattr(module_config, "serial"):
                        serial = module_config.serial
                        config["serial_module"] = {
                            "enabled": getattr(serial, "enabled", False),
                            "echo": getattr(serial, "echo", False),
                            "rxd": getattr(serial, "rxd", 0),
                            "txd": getattr(serial, "txd", 0),
                            "baud": getattr(serial, "baud", 0),
                            "timeout": getattr(serial, "timeout", 0),
                            "mode": getattr(serial, "mode", 0),
                        }
                    
                    # External Notification Module
                    if hasattr(module_config, "external_notification"):
                        ext_notif = module_config.external_notification
                        config["external_notification"] = {
                            "enabled": getattr(ext_notif, "enabled", False),
                            "output_ms": getattr(ext_notif, "output_ms", 0),
                            "output": getattr(ext_notif, "output", 0),
                            "output_vibra": getattr(ext_notif, "output_vibra", 0),
                            "output_buzzer": getattr(ext_notif, "output_buzzer", 0),
                            "active": getattr(ext_notif, "active", False),
                            "alert_message": getattr(ext_notif, "alert_message", False),
                            "alert_bell": getattr(ext_notif, "alert_bell", False),
                        }
                    
                    # Store & Forward Module
                    if hasattr(module_config, "store_forward"):
                        store_fwd = module_config.store_forward
                        config["store_forward"] = {
                            "enabled": getattr(store_fwd, "enabled", False),
                            "heartbeat": getattr(store_fwd, "heartbeat", False),
                            "records": getattr(store_fwd, "records", 0),
                            "history_return_max": getattr(store_fwd, "history_return_max", 0),
                            "history_return_window": getattr(store_fwd, "history_return_window", 0),
                        }
                    
                    # Range Test Module
                    if hasattr(module_config, "range_test"):
                        range_test = module_config.range_test
                        config["range_test"] = {
                            "enabled": getattr(range_test, "enabled", False),
                            "sender": getattr(range_test, "sender", 0),
                            "save": getattr(range_test, "save", False),
                        }
                    
                    # Telemetry Module
                    if hasattr(module_config, "telemetry"):
                        telemetry = module_config.telemetry
                        config["telemetry"] = {
                            "device_update_interval": getattr(telemetry, "device_update_interval", 0),
                            "environment_update_interval": getattr(telemetry, "environment_update_interval", 0),
                            "environment_measurement_enabled": getattr(telemetry, "environment_measurement_enabled", False),
                            "environment_screen_enabled": getattr(telemetry, "environment_screen_enabled", False),
                            "environment_display_fahrenheit": getattr(telemetry, "environment_display_fahrenheit", False),
                        }
                    
                    # Canned Message Module
                    if hasattr(module_config, "canned_message"):
                        canned = module_config.canned_message
                        config["canned_message"] = {
                            "enabled": getattr(canned, "enabled", False),
                            "allow_input_source": getattr(canned, "allow_input_source", ""),
                            "send_bell": getattr(canned, "send_bell", False),
                        }
                    
                    # Audio Module
                    if hasattr(module_config, "audio"):
                        audio = module_config.audio
                        config["audio"] = {
                            "enabled": getattr(audio, "enabled", False),
                            "codec2_enabled": getattr(audio, "codec2_enabled", False),
                            "pttt_gpio": getattr(audio, "pttt_gpio", 0),
                        }
                    
                    # Remote Hardware Module
                    if hasattr(module_config, "remote_hardware"):
                        remote_hw = module_config.remote_hardware
                        config["remote_hardware"] = {
                            "enabled": getattr(remote_hw, "enabled", False),
                            "allow_undefined_pin_access": getattr(remote_hw, "allow_undefined_pin_access", False),
                        }
                    
                    # Neighbor Info Module
                    if hasattr(module_config, "neighbor_info"):
                        neighbor = module_config.neighbor_info
                        config["neighbor_info"] = {
                            "enabled": getattr(neighbor, "enabled", False),
                            "update_interval": getattr(neighbor, "update_interval", 0),
                        }
                    
                    # Ambient Lighting Module
                    if hasattr(module_config, "ambient_lighting"):
                        ambient = module_config.ambient_lighting
                        config["ambient_lighting"] = {
                            "enabled": getattr(ambient, "enabled", False),
                            "current": getattr(ambient, "current", 0),
                        }
                    
                    # Detection Sensor Module
                    if hasattr(module_config, "detection_sensor"):
                        detection = module_config.detection_sensor
                        config["detection_sensor"] = {
                            "enabled": getattr(detection, "enabled", False),
                            "minimum_broadcast_secs": getattr(detection, "minimum_broadcast_secs", 0),
                            "state_broadcast_secs": getattr(detection, "state_broadcast_secs", 0),
                            "monitor_pin": getattr(detection, "monitor_pin", 0),
                            "detection_triggered_high": getattr(detection, "detection_triggered_high", False),
                            "use_pullup": getattr(detection, "use_pullup", False),
                        }
                    
                    # Paxcounter Module
                    if hasattr(module_config, "paxcounter"):
                        paxcounter = module_config.paxcounter
                        config["paxcounter"] = {
                            "enabled": getattr(paxcounter, "enabled", False),
                            "paxcounter_update_interval": getattr(paxcounter, "paxcounter_update_interval", 0),
                        }

        except Exception as e:
            logger.warning(f"Failed to extract full config: {e}")

        return config

    async def check_node_reachability(self, node: Node, serial_port: str | None = None) -> NodeStatus:
        """Check if a node is reachable.

        Args:
            node: Node to check
            serial_port: Serial port to check (required for managed nodes)

        Returns:
            NodeStatus object with reachability info
        """
        if not serial_port:
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
                None, self._check_port_reachable, serial_port
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

    async def import_heard_nodes(self, connection_string: str, managed_node_id: str) -> tuple[list[Node], list[HeardHistory]]:
        """Import all heard nodes from a connected device.

        Args:
            connection_string: Connection string (serial port or tcp://host:port)
            managed_node_id: ID of the managed node doing the hearing

        Returns:
            Tuple of (heard_nodes, heard_history_entries)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._import_heard_nodes_blocking, connection_string, managed_node_id)

    def _import_heard_nodes_blocking(self, connection_string: str, managed_node_id: str) -> tuple[list[Node], list[HeardHistory]]:
        """Blocking import of heard nodes (runs in thread pool).

        Args:
            connection_string: Connection string (serial port or tcp://host:port)
            managed_node_id: ID of the managed node doing the hearing

        Returns:
            Tuple of (heard_nodes, heard_history_entries)
        """
        # Determine connection type and create appropriate interface
        if connection_string.startswith("tcp://"):
            import meshtastic.tcp_interface
            # Extract host:port from tcp://host:port
            host_port = connection_string[6:]  # Remove "tcp://"
            interface = meshtastic.tcp_interface.TCPInterface(hostname=host_port)
        else:
            import meshtastic.serial_interface
            interface = meshtastic.serial_interface.SerialInterface(connection_string)

        heard_nodes = []
        heard_history = []
        timestamp = datetime.now()

        # Get my node number to exclude it
        my_node_num = interface.myInfo.my_node_num
        my_node_id = f"!{my_node_num:08x}"

        # Iterate through all nodes in the interface
        for node_id, node_data in interface.nodes.items():
            # Skip the managed node itself
            if node_id == my_node_id:
                continue

            user = node_data.get("user", {})

            # Create heard node
            heard_node = Node(
                id=node_id,
                short_name=user.get("shortName", "?"),
                long_name=user.get("longName", "Unknown"),
                hw_model=user.get("hwModel"),
                firmware_version=None,  # Don't have firmware version for heard nodes
                last_seen=datetime.fromtimestamp(node_data.get("lastHeard", timestamp.timestamp())),
                is_active=True,
                snr=node_data.get("snr"),
                hops_away=node_data.get("hopsAway"),
                config={},  # No config for heard nodes
            )
            heard_nodes.append(heard_node)

            # Create history entry
            position = node_data.get("position", {})
            history_entry = HeardHistory(
                node_id=node_id,
                long_name=user.get("longName", "Unknown"),
                seen_by=managed_node_id,
                timestamp=timestamp,
                snr=node_data.get("snr"),
                hops_away=node_data.get("hopsAway"),
                position_lat=position.get("latitude") if position else None,
                position_lon=position.get("longitude") if position else None,
            )
            heard_history.append(history_entry)

        interface.close()
        return heard_nodes, heard_history