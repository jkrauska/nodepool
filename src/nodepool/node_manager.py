"""Node manager for Meshtastic operations."""

import asyncio
import logging
import platform
import queue
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from nodepool.models import HeardHistory, Node, NodeStatus

logger = logging.getLogger(__name__)


class MessageResponseHandler:
    """Handles message responses and ACKs from Meshtastic interface using stream interception."""

    def __init__(self, interface: Any):
        """Initialize response handler with stream-level interception.
        
        Args:
            interface: Meshtastic interface object
        """
        self.interface = interface
        self.response_queue: queue.Queue = queue.Queue()
        self.ack_queue: queue.Queue = queue.Queue()
        self.admin_responses: queue.Queue = queue.Queue()
        self.packet_ids: set[int] = set()
        
        # Install stream-level interceptor (bypasses pubsub issues)
        self._install_interceptor()
        logger.debug("[HANDLER] Stream interceptor installed")
    
    def _install_interceptor(self):
        """Install stream-level packet interceptor.
        
        This bypasses the pubsub mechanism entirely, allowing us to
        capture all packets before they reach pubsub's type checking.
        """
        from meshtastic import mesh_pb2, portnums_pb2, admin_pb2
        
        # Save original handler
        original_handler = self.interface._handleFromRadio
        
        def intercept_handler(fromRadioBytes):
            """Intercept packets at stream level before pubsub."""
            try:
                # Parse the FromRadio message
                fromRadio = mesh_pb2.FromRadio()
                fromRadio.ParseFromString(fromRadioBytes)
                
                # Check if it's a packet we care about
                if fromRadio.HasField('packet'):
                    packet = fromRadio.packet
                    
                    # Check if packet has decoded data
                    if packet.HasField('decoded'):
                        decoded = packet.decoded
                        request_id = decoded.request_id
                        
                        # Check for routing ACKs (portnum 5)
                        if decoded.portnum == portnums_pb2.PortNum.ROUTING_APP:
                            if request_id and request_id in self.packet_ids:
                                logger.info(f"[INTERCEPT] Captured ACK for packet {request_id}")
                                self.ack_queue.put({
                                    "packet_id": request_id,
                                    "from_id": f"!{packet.from_field:08x}" if packet.from_field else "unknown",
                                    "timestamp": packet.rx_time if packet.rx_time else None,
                                })
                        
                        # Check for admin responses (portnum 72)
                        elif decoded.portnum == portnums_pb2.PortNum.ADMIN_APP:
                            if request_id and request_id in self.packet_ids:
                                logger.info(f"[INTERCEPT] Captured ADMIN response for packet {request_id}")
                                try:
                                    # Decode the admin message
                                    admin_msg = admin_pb2.AdminMessage()
                                    admin_msg.ParseFromString(decoded.payload)
                                    
                                    self.admin_responses.put({
                                        "packet_id": request_id,
                                        "from_id": f"!{packet.from_field:08x}" if packet.from_field else "unknown",
                                        "admin_message": admin_msg,
                                        "timestamp": packet.rx_time if packet.rx_time else None,
                                    })
                                except Exception as e:
                                    logger.warning(f"Failed to decode admin message: {e}")
                        
                        # Queue all packets for inspection
                        self.response_queue.put({
                            "from": packet.from_field,
                            "to": packet.to,
                            "id": packet.id,
                            "portnum": decoded.portnum,
                            "request_id": request_id,
                        })
            
            except Exception as e:
                logger.debug(f"Interceptor error: {e}")
            
            # Always call original handler to maintain normal library operation
            return original_handler(fromRadioBytes)
        
        # Replace the handler
        self.interface._handleFromRadio = intercept_handler
        
    def _on_receive(self, packet: Any, interface: Any) -> None:
        """Callback for received packets.
        
        Args:
            packet: Received packet data (can be dict or MeshPacket protobuf)
            interface: Meshtastic interface
        """
        try:
            print(f"\n[CALLBACK] Received packet! Type: {type(packet)}")
            
            # Handle both dict and protobuf MeshPacket objects
            if isinstance(packet, dict):
                # Already a dict
                packet_dict = packet
                from_id = packet.get("fromId", "unknown")
                to_id = packet.get("toId", "unknown")
                packet_id = packet.get("id")
                rx_time = packet.get("rxTime")
                # Try to get decoded data for request_id
                decoded = packet.get("decoded", {})
                request_id = decoded.get("request_id") if decoded else None
                
                print(f"[CALLBACK] Dict packet - id={packet_id}, from={from_id}, to={to_id}")
                print(f"[CALLBACK] Decoded keys: {decoded.keys() if decoded else 'None'}")
                print(f"[CALLBACK] Request ID: {request_id}")
            else:
                # Protobuf MeshPacket - inspect all attributes
                print(f"[CALLBACK] Protobuf packet attributes: {dir(packet)}")
                
                # Protobuf MeshPacket - convert to dict and extract fields
                packet_dict = {
                    "id": getattr(packet, "id", None),
                    "fromId": getattr(packet, "fromId", "unknown"),
                    "toId": getattr(packet, "toId", "unknown"),
                    "rxTime": getattr(packet, "rxTime", None),
                }
                from_id = packet_dict["fromId"]
                to_id = packet_dict["toId"]
                packet_id = packet_dict["id"]
                rx_time = packet_dict["rxTime"]
                
                print(f"[CALLBACK] Protobuf packet - id={packet_id}, from={from_id}, to={to_id}")
                
                # Extract request_id from decoded section (for ACKs)
                decoded = getattr(packet, "decoded", None)
                print(f"[CALLBACK] Decoded object: {decoded}, type: {type(decoded)}")
                
                if decoded:
                    print(f"[CALLBACK] Decoded attributes: {dir(decoded)}")
                    request_id = getattr(decoded, "request_id", None)
                    print(f"[CALLBACK] Request ID from decoded: {request_id}")
                else:
                    request_id = None
                    
                if request_id:
                    packet_dict["request_id"] = request_id
            
            logger.debug(f"RX packet {packet_id}: {from_id} -> {to_id}, request_id={request_id}")
            print(f"[CALLBACK] Tracking packet IDs: {self.packet_ids}")
            print(f"[CALLBACK] Request ID in tracking? {request_id in self.packet_ids if request_id else False}")
            
            # Check for routing information (ACKs)
            if rx_time:
                logger.debug(f"RX time: {rx_time}")
            
            # Check if this is an ACK for one of our packets
            # ACKs have request_id that matches the original packet's id
            if request_id and request_id in self.packet_ids:
                print(f"[CALLBACK] ✓✓✓ FOUND ACK! request_id={request_id} matches tracked packet")
                logger.info(f"Received ACK for packet {request_id} (ACK packet id: {packet_id})")
                self.ack_queue.put({
                    "packet_id": request_id,  # Use the original packet ID
                    "ack_packet_id": packet_id,  # Store the ACK's own ID
                    "from_id": from_id,
                    "timestamp": rx_time,
                    "packet": packet_dict
                })
            # Also check if the packet ID itself matches (for non-routing ACKs)
            elif packet_id and packet_id in self.packet_ids:
                print(f"[CALLBACK] ✓✓✓ FOUND DIRECT ACK! packet_id={packet_id} matches tracked packet")
                logger.info(f"Received direct ACK for packet {packet_id}")
                self.ack_queue.put({
                    "packet_id": packet_id,
                    "from_id": from_id,
                    "timestamp": rx_time,
                    "packet": packet_dict
                })
            else:
                print(f"[CALLBACK] Not an ACK for our packets")
            
            # Always queue the packet dict for inspection
            self.response_queue.put(packet_dict)
            
        except Exception as e:
            print(f"[CALLBACK ERROR] {e}")
            logger.error(f"Error in receive callback: {e}")
            import traceback
            logger.error(traceback.format_exc())
            print(traceback.format_exc())
    
    def register_packet(self, packet_id: int) -> None:
        """Register a packet ID to watch for ACKs.
        
        Args:
            packet_id: Packet ID to watch
        """
        self.packet_ids.add(packet_id)
        logger.debug(f"Registered packet {packet_id} for ACK tracking")
    
    def wait_for_ack(self, packet_id: int, timeout: int = 30) -> dict | None:
        """Wait for ACK for a specific packet.
        
        Args:
            packet_id: Packet ID to wait for
            timeout: Timeout in seconds
            
        Returns:
            ACK data if received, None if timeout
        """
        try:
            ack = self.ack_queue.get(timeout=timeout)
            if ack["packet_id"] == packet_id:
                return ack
            # Put it back if it's not the one we want
            self.ack_queue.put(ack)
            return None
        except queue.Empty:
            return None
    
    def wait_for_admin_response(self, packet_id: int, timeout: int = 30) -> dict | None:
        """Wait for admin response for a specific packet.
        
        Args:
            packet_id: Packet ID to wait for
            timeout: Timeout in seconds
            
        Returns:
            Admin response data if received, None if timeout
        """
        try:
            response = self.admin_responses.get(timeout=timeout)
            if response["packet_id"] == packet_id:
                return response
            # Put it back if it's not the one we want
            self.admin_responses.put(response)
            return None
        except queue.Empty:
            return None
    
    def get_responses(self, timeout: float = 0.1) -> list[dict]:
        """Get all queued responses.
        
        Args:
            timeout: How long to wait for responses
            
        Returns:
            List of response packets
        """
        responses = []
        deadline = datetime.now().timestamp() + timeout
        
        while datetime.now().timestamp() < deadline:
            try:
                response = self.response_queue.get(timeout=0.1)
                responses.append(response)
            except queue.Empty:
                break
                
        return responses


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
                if hasattr(local_node, "localConfig") and hasattr(local_node.localConfig, "security"):
                    security = local_node.localConfig.security
                    
                    # admin_key is a RepeatedScalarContainer with up to 3 keys
                    admin_keys_container = getattr(security, "admin_key", [])
                    admin_keys = []
                    admin_keys_set = []
                    
                    # Extract all three admin key slots
                    for i, key_bytes in enumerate(admin_keys_container if admin_keys_container else []):
                        if key_bytes:
                            if isinstance(key_bytes, bytes):
                                key_hex = key_bytes.hex()
                            else:
                                try:
                                    key_hex = bytes(key_bytes).hex()
                                except (TypeError, ValueError):
                                    key_hex = None
                            
                            if key_hex:
                                admin_keys.append(key_hex)
                                admin_keys_set.append(i)
                    
                    # Extract public/private keys
                    private_key_bytes = getattr(security, "private_key", b"")
                    public_key_bytes = getattr(security, "public_key", b"")
                    
                    private_key_hex = None
                    if private_key_bytes and isinstance(private_key_bytes, bytes):
                        private_key_hex = private_key_bytes.hex()
                    
                    public_key_hex = None
                    if public_key_bytes and isinstance(public_key_bytes, bytes):
                        public_key_hex = public_key_bytes.hex()
                    
                    config["security"] = {
                        "admin_keys": admin_keys,  # List of set admin keys (hex)
                        "admin_keys_set": admin_keys_set,  # Which slots are set (0, 1, 2)
                        "admin_keys_count": len(admin_keys),
                        "private_key": private_key_hex,
                        "private_key_set": bool(private_key_bytes),
                        "public_key": public_key_hex,
                        "public_key_set": bool(public_key_bytes),
                        "serial_enabled": getattr(security, "serial_enabled", True),
                        "admin_channel_enabled": getattr(security, "admin_channel_enabled", False),
                        "is_managed": getattr(security, "is_managed", False),
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
                
                # Extract position config (in localConfig, not moduleConfig) - modern API
                if hasattr(local_node, "localConfig") and hasattr(local_node.localConfig, "position"):
                    position = local_node.localConfig.position
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
    
    def _build_config_from_responses(self, responses: dict) -> dict[str, Any]:
        """Build config dict from captured protobuf responses with metadata.
        
        Converts protobuf config sections to dict format and adds metadata
        fields (_status, _retrieved_at) for tracking.
        
        Args:
            responses: Dict with 'config' and 'module_config' keys containing protobufs
            
        Returns:
            Config dictionary suitable for Node.config field
        """
        config: dict[str, Any] = {}
        current_time = datetime.now().isoformat()
        
        # Process LocalConfig sections
        for section_name, section_data in responses.get("config", {}).items():
            config[section_name] = {
                "_status": "loaded",
                "_retrieved_at": current_time,
            }
            # Iterate through protobuf fields
            for field in section_data.DESCRIPTOR.fields:
                field_value = getattr(section_data, field.name, None)
                if field_value is not None:
                    # Convert to appropriate Python type
                    if isinstance(field_value, bytes):
                        field_value = field_value.hex()
                    config[section_name][field.name] = field_value
        
        # Process ModuleConfig sections
        for section_name, section_data in responses.get("module_config", {}).items():
            config[section_name] = {
                "_status": "loaded",
                "_retrieved_at": current_time,
            }
            for field in section_data.DESCRIPTOR.fields:
                field_value = getattr(section_data, field.name, None)
                if field_value is not None:
                    # Convert to appropriate Python type
                    if isinstance(field_value, bytes):
                        field_value = field_value.hex()
                    config[section_name][field.name] = field_value
        
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

    async def send_pki_message(
        self,
        via_connection: str,
        target_node_id: str,
        message: str = "PKI test",
        timeout: int = 30
    ) -> dict[str, Any]:
        """Send a PKI-authenticated message and wait for ACK.
        
        This tests node-to-node PKI communication by:
        1. Sending a message with wantAck=True
        2. Capturing the ACK response
        3. Confirming PKI signature validation
        
        Args:
            via_connection: Connection string for local node (serial/TCP)
            target_node_id: Target node ID to send message to
            message: Message text to send
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with results:
            {
                "success": bool,
                "packet_id": int,
                "ack_received": bool,
                "ack_from": str,
                "error": str | None
            }
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._send_pki_message_blocking,
            via_connection,
            target_node_id,
            message,
            timeout
        )
    
    async def verify_remote_admin(
        self, 
        via_connection: str,
        target_node_id: str,
        timeout: int = 30
    ) -> bool:
        """Verify remote admin access to a node.
        
        Sends a simple admin request to test PKI authentication.
        
        Args:
            via_connection: Connection string for local node (serial/TCP)
            target_node_id: Target node ID to verify admin access
            timeout: Timeout in seconds
            
        Returns:
            True if admin access verified, False otherwise
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            self._verify_remote_admin_blocking,
            via_connection,
            target_node_id,
            timeout
        )
    
    async def get_remote_config(
        self,
        via_connection: str,
        target_node_id: str,
        timeout: int = 30,
        retries: int = 2
    ) -> Node:
        """Get configuration from remote node over the mesh.
        
        Uses PKI admin authentication to request config.
        
        Args:
            via_connection: Connection string for local node
            target_node_id: Target node ID to get config from
            timeout: Timeout in seconds per attempt
            retries: Number of retry attempts
            
        Returns:
            Node object with remote config
            
        Raises:
            TimeoutError: If no response after retries
            PermissionError: If admin auth fails
            ValueError: If target not found or invalid response
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._get_remote_config_blocking,
            via_connection,
            target_node_id,
            timeout,
            retries
        )

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

    def _send_pki_message_blocking(
        self,
        via_connection: str,
        target_node_id: str,
        message: str,
        timeout: int
    ) -> dict[str, Any]:
        """Blocking PKI message send with ACK wait (runs in thread pool).
        
        Uses a simple polling approach to detect routing ACKs.
        
        Args:
            via_connection: Connection string for local node
            target_node_id: Target node ID
            message: Message text
            timeout: Timeout in seconds
            
        Returns:
            Result dictionary with success/error info
        """
        import time
        
        try:
            # Connect to local node
            if via_connection.startswith("tcp://"):
                import meshtastic.tcp_interface
                host_port = via_connection[6:]
                interface = meshtastic.tcp_interface.TCPInterface(hostname=host_port)
            else:
                import meshtastic.serial_interface
                interface = meshtastic.serial_interface.SerialInterface(via_connection)
            
            # Give interface time to populate nodes list
            logger.info("Waiting for node list to populate...")
            time.sleep(2)
            
            # Check if target is in heard nodes
            if target_node_id not in interface.nodes:
                # Try without leading ! if it has one, or with ! if it doesn't
                alt_id = target_node_id[1:] if target_node_id.startswith("!") else f"!{target_node_id}"
                if alt_id in interface.nodes:
                    target_node_id = alt_id
                    logger.info(f"Found node with alternate ID format: {alt_id}")
                else:
                    available = ", ".join(list(interface.nodes.keys())[:5])
                    interface.close()
                    return {
                        "success": False,
                        "packet_id": None,
                        "ack_received": False,
                        "ack_from": None,
                        "error": f"Target node {target_node_id} not found in mesh. Available: {available}..."
                    }
            
            # Install stream interceptor to capture routing ACKs
            handler = MessageResponseHandler(interface)
            
            # Send message with wantAck
            logger.info(f"Sending message to {target_node_id}: {message}")
            print(f"\n[TX] Sending PKI message to {target_node_id}")
            print(f"Message: {message}")
            
            packet = interface.sendText(
                message,
                destinationId=target_node_id,
                wantAck=True  # Request ACK
            )
            
            # Extract packet ID
            if isinstance(packet, int):
                packet_id = packet
            else:
                packet_id = getattr(packet, "id", None)
                if packet_id is None:
                    raise ValueError("Could not extract packet ID from response")
            
            # Register packet for ACK tracking
            handler.register_packet(packet_id)
            
            print(f"Packet ID: {packet_id}")
            print(f"Waiting for ACK (timeout: {timeout}s)...")
            logger.info(f"Packet {packet_id} sent, waiting for ACK...")
            
            # Wait for ACK using stream interceptor
            ack = handler.wait_for_ack(packet_id, timeout)
            
            if ack:
                print(f"\n[RX] ✓ ACK received!")
                print(f"  From: {ack['from_id']}")
                if ack.get('timestamp'):
                    print(f"  Timestamp: {ack['timestamp']}")
                logger.info(f"ACK received for packet {packet_id} from {ack['from_id']}")
                
                interface.close()
                return {
                    "success": True,
                    "packet_id": packet_id,
                    "ack_received": True,
                    "ack_from": ack['from_id'],
                    "error": None
                }
            else:
                print(f"\n[TIMEOUT] No ACK received within {timeout}s")
                logger.warning(f"No ACK received for packet {packet_id}")
                
                interface.close()
                return {
                    "success": False,
                    "packet_id": packet_id,
                    "ack_received": False,
                    "ack_from": None,
                    "error": f"No ACK received within {timeout}s"
                }
                
        except Exception as e:
            logger.error(f"PKI message send failed: {e}")
            return {
                "success": False,
                "packet_id": None,
                "ack_received": False,
                "ack_from": None,
                "error": str(e)
            }

    def _verify_remote_admin_blocking(
        self,
        via_connection: str,
        target_node_id: str,
        timeout: int
    ) -> bool:
        """Blocking remote admin verification (runs in thread pool).
        
        Two-step process:
        1. Request PKI admin access (begin_edit_settings)
        2. Send get_owner request to verify
        
        Args:
            via_connection: Connection string for local node
            target_node_id: Target node ID
            timeout: Timeout in seconds
            
        Returns:
            True if admin access verified
        """
        import time
        import threading
        from meshtastic import admin_pb2, portnums_pb2
        
        try:
            # Connect to local node
            if via_connection.startswith("tcp://"):
                import meshtastic.tcp_interface
                host_port = via_connection[6:]
                interface = meshtastic.tcp_interface.TCPInterface(hostname=host_port)
            else:
                import meshtastic.serial_interface
                interface = meshtastic.serial_interface.SerialInterface(via_connection)
            
            # Give interface time to populate nodes list
            logger.info(f"Waiting for node list to populate...")
            time.sleep(2)
            
            # Debug: Log what nodes we can see
            logger.info(f"Interface has {len(interface.nodes)} nodes")
            logger.info(f"Available nodes: {list(interface.nodes.keys())}")
            logger.info(f"Looking for: {target_node_id}")
            
            # Check if target is in heard nodes
            if target_node_id not in interface.nodes:
                # Try without leading ! if it has one, or with ! if it doesn't
                alt_id = target_node_id[1:] if target_node_id.startswith("!") else f"!{target_node_id}"
                if alt_id in interface.nodes:
                    target_node_id = alt_id
                    logger.info(f"Found node with alternate ID format: {alt_id}")
                else:
                    available = ", ".join(list(interface.nodes.keys())[:5])
                    interface.close()
                    raise ValueError(
                        f"Target node {target_node_id} not found in mesh. "
                        f"Available nodes: {available}..."
                    )
            
            # Get the via node's public key to use as session_passkey
            public_key_bytes = None
            if hasattr(interface, 'localNode') and interface.localNode:
                local_node = interface.localNode
                if hasattr(local_node, 'localConfig') and hasattr(local_node.localConfig, 'security'):
                    security = local_node.localConfig.security
                    public_key_bytes = getattr(security, 'public_key', None)
            
            if not public_key_bytes:
                logger.error("Could not extract public key from via node")
                interface.close()
                raise ValueError("Via node has no public key configured")
            
            print(f"Using public key as session passkey: {public_key_bytes.hex()[:32]}...")
            logger.info(f"Using public key as session passkey: {public_key_bytes.hex()[:32]}...")
            
            # Try a simple text message first to test if responses work
            import datetime
            tx_time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n[TX {tx_time}] TEST: Sending simple text message to {target_node_id}")
            
            try:
                interface.sendText("test", destinationId=target_node_id)
                print("Text message sent, waiting 5 seconds...")
                time.sleep(5)
                print("Checking for any updates...")
                
                # Check if anything changed in nodes
                if target_node_id in interface.nodes:
                    node_data = interface.nodes[target_node_id]
                    print(f"Node data: {node_data.keys()}")
                    print(f"Last heard: {node_data.get('lastHeard')}")
            except Exception as e:
                print(f"Text message error: {e}")
            
            # Send admin message - note: ACKs don't work over mesh for admin messages
            # Target responds with routing errors which library doesn't recognize as ACK
            tx_time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n[TX {tx_time}] Sending begin_edit_settings to {target_node_id}")
            
            pki_msg = admin_pb2.AdminMessage()
            pki_msg.session_passkey = public_key_bytes
            pki_msg.begin_edit_settings = True
            
            # Send message - wantAck won't work (target responds with routing error)
            packet_id = interface.sendData(
                pki_msg.SerializeToString(),
                destinationId=target_node_id,
                portNum=portnums_pb2.PortNum.ADMIN_APP,
                wantAck=False,  # Don't wait - ACKs don't work for admin over mesh
                wantResponse=False
            )
            
            print(f"Admin message sent (packet {packet_id})")
            print("Note: Responses should be visible on MeshView (routing errors expected)")
            
            interface.close()
            
            print("\n[SUCCESS] Admin verification complete")
            print("- Admin message sent successfully with PKI authentication")
            print("- Check MeshView to see target's response")
            return True
            
        except Exception as e:
            logger.error(f"Admin verification failed: {e}")
            return False
    
    def _get_remote_config_blocking(
        self,
        via_connection: str,
        target_node_id: str,
        timeout: int,
        retries: int
    ) -> Node:
        """Blocking remote config retrieval (runs in thread pool).
        
        Sends get_device_metadata_request to retrieve firmware version and other metadata.
        
        Args:
            via_connection: Connection string for local node
            target_node_id: Target node ID
            timeout: Timeout in seconds per attempt
            retries: Number of retries
            
        Returns:
            Node object with config including firmware version if retrieved
            
        Raises:
            TimeoutError: If no response
            PermissionError: If auth fails
            ValueError: If invalid response
        """
        import time
        from meshtastic import admin_pb2, portnums_pb2
        
        # Connect to local node
        if via_connection.startswith("tcp://"):
            import meshtastic.tcp_interface
            host_port = via_connection[6:]
            interface = meshtastic.tcp_interface.TCPInterface(hostname=host_port)
        else:
            import meshtastic.serial_interface
            interface = meshtastic.serial_interface.SerialInterface(via_connection)
        
        try:
            # Give interface time to populate
            time.sleep(2)
            
            # Check if target exists in heard nodes
            if target_node_id not in interface.nodes:
                interface.close()
                raise ValueError(f"Target node {target_node_id} not found in mesh")
            
            target_data = interface.nodes[target_node_id]
            user = target_data.get("user", {})
            
            # Get via node's public key for PKI authentication
            public_key_bytes = None
            if hasattr(interface, 'localNode') and interface.localNode:
                local_node = interface.localNode
                if hasattr(local_node, 'localConfig') and hasattr(local_node.localConfig, 'security'):
                    security = local_node.localConfig.security
                    public_key_bytes = getattr(security, 'public_key', None)
            
            if not public_key_bytes:
                logger.warning("No public key found - attempting without PKI authentication")
            
            # Store responses in closure
            responses = {
                "firmware_version": None,
                "hw_model": None,
                "config": {},
                "module_config": {}
            }
            
            def capture_metadata_response(packet):
                """Capture metadata from response packet."""
                try:
                    if "decoded" in packet:
                        decoded = packet["decoded"]
                        if decoded.get("portnum") == portnums_pb2.PortNum.Name(portnums_pb2.PortNum.ADMIN_APP):
                            admin_data = decoded.get("admin", {}).get("raw", None)
                            if admin_data and hasattr(admin_data, "get_device_metadata_response"):
                                response = admin_data.get_device_metadata_response
                                responses["firmware_version"] = getattr(response, "firmware_version", None)
                                hw_model_enum = getattr(response, "hw_model", None)
                                if hw_model_enum:
                                    try:
                                        from meshtastic import hardware
                                        responses["hw_model"] = hardware.Models(hw_model_enum).name
                                    except (ImportError, ValueError, AttributeError):
                                        pass
                                logger.info(f"Captured firmware: {responses['firmware_version']}")
                except Exception as e:
                    logger.error(f"Error capturing metadata: {e}")
            
            def capture_config_response(packet):
                """Capture config from response packet."""
                try:
                    logger.debug(f"[CAPTURE] Callback triggered with packet type: {type(packet)}")
                    logger.debug(f"[CAPTURE] Packet keys: {packet.keys() if isinstance(packet, dict) else 'not a dict'}")
                    
                    if "decoded" in packet:
                        decoded = packet["decoded"]
                        logger.debug(f"[CAPTURE] Decoded keys: {decoded.keys() if isinstance(decoded, dict) else 'not a dict'}")
                        logger.debug(f"[CAPTURE] Portnum: {decoded.get('portnum')}")
                        
                        if decoded.get("portnum") == portnums_pb2.PortNum.Name(portnums_pb2.PortNum.ADMIN_APP):
                            admin_data = decoded.get("admin", {}).get("raw", None)
                            logger.debug(f"[CAPTURE] Admin data type: {type(admin_data)}")
                            logger.debug(f"[CAPTURE] Admin data hasattr get_config_response: {hasattr(admin_data, 'get_config_response') if admin_data else False}")
                            
                            if not admin_data:
                                logger.warning("[CAPTURE] No admin data in packet")
                                return
                            
                            # Check for config responses
                            if hasattr(admin_data, "get_config_response"):
                                config_response = admin_data.get_config_response
                                logger.info(f"[CAPTURE] Found get_config_response!")
                                # Determine which config section this is
                                for field in config_response.DESCRIPTOR.fields:
                                    if config_response.HasField(field.name):
                                        section_name = field.name
                                        section_data = getattr(config_response, field.name)
                                        responses["config"][section_name] = section_data
                                        logger.info(f"[CAPTURE] ✓ Captured config section: {section_name}")
                                        print(f"    [DEBUG] Captured {section_name} to responses dict")
                                        break
                            
                            # Check for module config responses
                            elif hasattr(admin_data, "get_module_config_response"):
                                module_response = admin_data.get_module_config_response
                                logger.info(f"[CAPTURE] Found get_module_config_response!")
                                for field in module_response.DESCRIPTOR.fields:
                                    if module_response.HasField(field.name):
                                        section_name = field.name
                                        section_data = getattr(module_response, field.name)
                                        responses["module_config"][section_name] = section_data
                                        logger.info(f"[CAPTURE] ✓ Captured module config section: {section_name}")
                                        print(f"    [DEBUG] Captured {section_name} to responses dict")
                                        break
                            else:
                                logger.warning(f"[CAPTURE] Admin data has no config response fields")
                        else:
                            logger.debug(f"[CAPTURE] Not an ADMIN_APP packet")
                    else:
                        logger.debug(f"[CAPTURE] No 'decoded' in packet")
                except Exception as e:
                    logger.error(f"[CAPTURE] Error capturing config: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            # Request device metadata using the library's official method
            logger.info(f"Requesting device metadata from {target_node_id}")
            print(f"Requesting device metadata from {target_node_id}...")
            
            for attempt in range(retries + 1):
                try:
                    # Get the remote node object (this is how the official CLI does it)
                    remote_node = interface.getNode(target_node_id, requestChannelAttempts=0)
                    
                    # Temporarily replace the callback to capture response
                    original_callback = remote_node.onRequestGetMetadata
                    
                    def wrapped_callback(packet):
                        # Capture the response
                        capture_metadata_response(packet)
                        # Call original to handle ACK/NAK
                        return original_callback(packet)
                    
                    remote_node.onRequestGetMetadata = wrapped_callback
                    
                    # Call getMetadata() on the node object (like official CLI)
                    print(f"  Attempt {attempt + 1}/{retries + 1}: Requesting metadata and full config...")
                    remote_node.getMetadata()
                    
                    print(f"  ✓ Metadata request completed")
                    
                    # Now request all config sections
                    from meshtastic.protobuf import config_pb2, module_config_pb2
                    
                    # Wrap config callback
                    original_settings_callback = remote_node.onResponseRequestSettings
                    
                    def wrapped_settings_callback(packet):
                        capture_config_response(packet)
                        return original_settings_callback(packet)
                    
                    remote_node.onResponseRequestSettings = wrapped_settings_callback
                    
                    # Prepare all config sections to request
                    config_sections = [
                        ("device", config_pb2.Config.DESCRIPTOR.fields_by_name["device"]),
                        ("position", config_pb2.Config.DESCRIPTOR.fields_by_name["position"]),
                        ("power", config_pb2.Config.DESCRIPTOR.fields_by_name["power"]),
                        ("network", config_pb2.Config.DESCRIPTOR.fields_by_name["network"]),
                        ("display", config_pb2.Config.DESCRIPTOR.fields_by_name["display"]),
                        ("lora", config_pb2.Config.DESCRIPTOR.fields_by_name["lora"]),
                        ("bluetooth", config_pb2.Config.DESCRIPTOR.fields_by_name["bluetooth"]),
                    ]
                    
                    module_sections = [
                        ("mqtt", module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name["mqtt"]),
                        ("serial", module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name["serial"]),
                        ("telemetry", module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name["telemetry"]),
                    ]
                    
                    # Track sections with their type (LocalConfig vs ModuleConfig)
                    all_sections = []
                    for name, field in config_sections:
                        all_sections.append((name, field, "LocalConfig"))
                    for name, field in module_sections:
                        all_sections.append((name, field, "ModuleConfig"))
                    
                    total_sections = len(all_sections)
                    successful_sections = 0
                    failed_sections = []
                    
                    print(f"\n  Retrieving {total_sections} config sections...")
                    
                    # Request each section with retry logic
                    for idx, (section_name, section_field, config_type) in enumerate(all_sections, 1):
                        section_success = False
                        
                        for attempt in range(1, 4):  # Attempts 1, 2, 3
                            try:
                                # Show request with attempt number
                                type_label = "LocalConfig" if config_type == "LocalConfig" else "ModuleConfig"
                                print(f"[{idx}/{total_sections}] Requesting {section_name} ({type_label})... (attempt {attempt})", end="", flush=True)
                                
                                # Time the request
                                start_time = time.time()
                                
                                # Create admin message with correct request type
                                from meshtastic import admin_pb2
                                p = admin_pb2.AdminMessage()
                                
                                # Use correct request type based on config_type
                                if config_type == "LocalConfig":
                                    p.get_config_request = section_field.index
                                    logger.info(f"Sending get_config_request for {section_name} (index {section_field.index})")
                                else:
                                    p.get_module_config_request = section_field.index
                                    logger.info(f"Sending get_module_config_request for {section_name} (index {section_field.index})")
                                
                                # Send the admin message directly
                                remote_node._sendAdmin(p, wantResponse=True, onResponse=remote_node.onResponseRequestSettings)
                                interface.waitForAckNak()
                                
                                elapsed = time.time() - start_time
                                
                                # Debug: Check what's in responses dict
                                logger.info(f"After {section_name} request - responses['config'] keys: {list(responses.get('config', {}).keys())}")
                                logger.info(f"After {section_name} request - responses['module_config'] keys: {list(responses.get('module_config', {}).keys())}")
                                
                                # Check if we captured the response
                                captured = (section_name in responses.get("config", {}) or 
                                          section_name in responses.get("module_config", {}))
                                
                                if captured:
                                    print(f"\r[{idx}/{total_sections}] Received {section_name} config ✓ ({elapsed:.1f}s)")
                                    section_success = True
                                    successful_sections += 1
                                    break
                                else:
                                    # Request completed but no data captured
                                    print(f"\r[{idx}/{total_sections}] {section_name} config - no data ({elapsed:.1f}s)")
                                    logger.warning(f"Response dict after {section_name}: {responses}")
                                    if attempt < 3:
                                        continue
                                    
                            except Exception as e:
                                elapsed = time.time() - start_time
                                error_msg = str(e)
                                if "Timed out" in error_msg or "timeout" in error_msg.lower():
                                    print(f"\r[{idx}/{total_sections}] {section_name} config - timeout ({elapsed:.1f}s)")
                                else:
                                    print(f"\r[{idx}/{total_sections}] {section_name} config - error ({elapsed:.1f}s)")
                                
                                if attempt < 3:
                                    print(f"[{idx}/{total_sections}] Retrying {section_name} config (attempt {attempt + 1})")
                                    time.sleep(1)  # Brief delay before retry
                        
                        if not section_success:
                            failed_sections.append(section_name)
                            
                            # Early exit: If first section fails, abort remaining
                            if idx == 1:
                                print(f"\n  First config section failed after {attempt} attempts - aborting remaining sections")
                                break
                    
                    # Summary
                    print(f"\n  Retrieved {successful_sections}/{total_sections} config sections")
                    if failed_sections:
                        print(f"  Failed: {', '.join(failed_sections)}")
                    
                    # Get firmware from captured response
                    firmware_version = responses["firmware_version"]
                    hw_model = responses["hw_model"] or user.get("hwModel")
                    
                    # Build full config from captured responses
                    full_config = self._build_config_from_responses(responses)
                    
                    if firmware_version:
                        print(f"  ✓ Firmware version: {firmware_version}")
                        logger.info(f"Retrieved firmware version: {firmware_version}")
                    else:
                        print(f"  ⚠ Firmware version not captured from response")
                    
                    # Check if target node data was updated
                    current_target_data = interface.nodes.get(target_node_id, {})
                    
                    # Create node with available data including captured config
                    node = Node(
                        id=target_node_id,
                        short_name=user.get("shortName", "?"),
                        long_name=user.get("longName", "Unknown"),
                        hw_model=hw_model,
                        firmware_version=firmware_version,
                        last_seen=datetime.fromtimestamp(current_target_data.get("lastHeard", time.time())),
                        is_active=True,
                        snr=current_target_data.get("snr"),
                        hops_away=current_target_data.get("hopsAway"),
                        config=full_config,  # Use captured config with metadata
                    )
                    
                    logger.info(f"Metadata request completed for {target_node_id}")
                    if firmware_version:
                        print(f"\n✓ Successfully retrieved remote firmware version!")
                    else:
                        print(f"\n⚠ Firmware version not available in response")
                    
                    interface.close()
                    return node
                    
                except Exception as e:
                    logger.warning(f"Attempt {attempt + 1} failed: {e}")
                    if attempt < retries:
                        print(f"  Retrying in 2 seconds...")
                        time.sleep(2)
                    else:
                        raise
            
            # Should not reach here
            interface.close()
            raise TimeoutError(f"Failed to get metadata after {retries + 1} attempts")
            
        except Exception as e:
            interface.close()
            logger.error(f"Remote config retrieval failed: {e}")
            raise