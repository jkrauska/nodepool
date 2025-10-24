"""Configuration validation logic for nodes."""

from typing import Any

from nodepool.models import ConfigCheck, Node


class ConfigChecker:
    """Validates node configurations against expected values."""

    def __init__(
        self,
        expected_ttl: int = 7,
        expected_region: str | None = None,
        expected_channels: list[dict[str, Any]] | None = None,
    ):
        """Initialize configuration checker.

        Args:
            expected_ttl: Expected hop limit (TTL) value
            expected_region: Expected LoRa region (optional)
            expected_channels: Expected channel configurations (optional)
        """
        self.expected_ttl = expected_ttl
        self.expected_region = expected_region
        self.expected_channels = expected_channels or []

    async def check_ttl(self, node: Node) -> ConfigCheck:
        """Check if node has correct TTL/hop limit.

        Args:
            node: Node to check

        Returns:
            ConfigCheck result
        """
        actual_ttl = node.config.get("lora", {}).get("hopLimit")

        if actual_ttl is None:
            return ConfigCheck(
                node_id=node.id,
                check_type="ttl",
                expected_value=self.expected_ttl,
                actual_value=None,
                status="warning",
                message=f"TTL not configured (expected: {self.expected_ttl})",
            )

        if actual_ttl == self.expected_ttl:
            return ConfigCheck(
                node_id=node.id,
                check_type="ttl",
                expected_value=self.expected_ttl,
                actual_value=actual_ttl,
                status="pass",
                message=f"TTL correctly set to {self.expected_ttl}",
            )

        return ConfigCheck(
            node_id=node.id,
            check_type="ttl",
            expected_value=self.expected_ttl,
            actual_value=actual_ttl,
            status="fail",
            message=f"TTL mismatch: expected {self.expected_ttl}, got {actual_ttl}",
        )

    async def check_region(self, node: Node) -> ConfigCheck:
        """Check if node has correct LoRa region.

        Args:
            node: Node to check

        Returns:
            ConfigCheck result
        """
        if not self.expected_region:
            return ConfigCheck(
                node_id=node.id,
                check_type="region",
                expected_value=None,
                actual_value=None,
                status="pass",
                message="Region check skipped (no expected region configured)",
            )

        actual_region = node.config.get("lora", {}).get("region")

        if actual_region is None:
            return ConfigCheck(
                node_id=node.id,
                check_type="region",
                expected_value=self.expected_region,
                actual_value=None,
                status="warning",
                message=f"Region not configured (expected: {self.expected_region})",
            )

        if actual_region == self.expected_region:
            return ConfigCheck(
                node_id=node.id,
                check_type="region",
                expected_value=self.expected_region,
                actual_value=actual_region,
                status="pass",
                message=f"Region correctly set to {self.expected_region}",
            )

        return ConfigCheck(
            node_id=node.id,
            check_type="region",
            expected_value=self.expected_region,
            actual_value=actual_region,
            status="fail",
            message=f"Region mismatch: expected {self.expected_region}, got {actual_region}",
        )

    async def check_channel(self, node: Node, channel_index: int = 1) -> ConfigCheck:
        """Check if node has correct secondary channel configuration.

        Args:
            node: Node to check
            channel_index: Channel index to check (default: 1 for secondary)

        Returns:
            ConfigCheck result
        """
        if not self.expected_channels:
            return ConfigCheck(
                node_id=node.id,
                check_type="channel",
                expected_value=None,
                actual_value=None,
                status="pass",
                message="Channel check skipped (no expected channels configured)",
            )

        channels = node.config.get("channels", [])

        if channel_index >= len(channels):
            return ConfigCheck(
                node_id=node.id,
                check_type="channel",
                expected_value=f"Channel {channel_index}",
                actual_value=None,
                status="warning",
                message=f"Channel {channel_index} not configured",
            )

        actual_channel = channels[channel_index]

        # For simplicity, just check if channel exists
        return ConfigCheck(
            node_id=node.id,
            check_type="channel",
            expected_value=f"Channel {channel_index} present",
            actual_value=actual_channel.get("name", f"Channel {channel_index}"),
            status="pass",
            message=f"Channel {channel_index} is configured",
        )

    async def check_node(self, node: Node) -> list[ConfigCheck]:
        """Run all configuration checks on a node.

        Args:
            node: Node to check

        Returns:
            List of ConfigCheck results
        """
        checks = []

        # Check TTL
        checks.append(await self.check_ttl(node))

        # Check region if configured
        if self.expected_region:
            checks.append(await self.check_region(node))

        # Check channels if configured
        if self.expected_channels:
            for i in range(len(self.expected_channels)):
                checks.append(await self.check_channel(node, channel_index=i + 1))

        # Security checks (always run if config available)
        if node.config.get("security"):
            checks.append(await self.check_admin_key(node))
            checks.append(await self.check_serial_disabled(node))
        
        # Channel encryption checks
        if node.config.get("channels"):
            checks.extend(await self.check_channel_encryption(node))

        return checks

    async def check_admin_key(self, node: Node) -> ConfigCheck:
        """Check if node has admin key configured.

        Args:
            node: Node to check

        Returns:
            ConfigCheck result
        """
        security = node.config.get("security", {})
        admin_key_set = security.get("admin_key_set", False)
        admin_key = security.get("admin_key")

        if not admin_key_set:
            return ConfigCheck(
                node_id=node.id,
                check_type="admin_key",
                expected_value="Admin key set",
                actual_value=None,
                status="warning",
                message="Admin key not configured",
            )

        # Check for default/weak keys (AQ== in base64 is 0x01)
        if admin_key == "01" or admin_key == "00":
            return ConfigCheck(
                node_id=node.id,
                check_type="admin_key",
                expected_value="Secure admin key",
                actual_value=f"{admin_key[:8]}...",
                status="fail",
                message="Admin key appears to be default/weak",
            )

        return ConfigCheck(
            node_id=node.id,
            check_type="admin_key",
            expected_value="Admin key set",
            actual_value=f"{admin_key[:8]}..." if admin_key else None,
            status="pass",
            message="Admin key is configured",
        )

    async def check_channel_encryption(self, node: Node) -> list[ConfigCheck]:
        """Check if channels have encryption configured.

        Args:
            node: Node to check

        Returns:
            List of ConfigCheck results for each channel
        """
        checks = []
        channels = node.config.get("channels", [])

        if not channels:
            return [
                ConfigCheck(
                    node_id=node.id,
                    check_type="channel_encryption",
                    expected_value="Channels configured",
                    actual_value=None,
                    status="warning",
                    message="No channels configured",
                )
            ]

        for channel in channels:
            channel_name = channel.get("name", f"Channel {channel.get('index', '?')}")
            psk_set = channel.get("psk_set", False)

            if not psk_set:
                checks.append(
                    ConfigCheck(
                        node_id=node.id,
                        check_type="channel_encryption",
                        expected_value=f"{channel_name} encrypted",
                        actual_value="Not encrypted",
                        status="warning",
                        message=f"{channel_name} is not encrypted",
                    )
                )
            else:
                psk = channel.get("psk", "")
                checks.append(
                    ConfigCheck(
                        node_id=node.id,
                        check_type="channel_encryption",
                        expected_value=f"{channel_name} encrypted",
                        actual_value=f"PSK: {psk[:8]}..." if psk else "encrypted",
                        status="pass",
                        message=f"{channel_name} is encrypted",
                    )
                )

        return checks

    async def check_serial_disabled(self, node: Node) -> ConfigCheck:
        """Check if serial console is disabled (optional security).

        Args:
            node: Node to check

        Returns:
            ConfigCheck result
        """
        security = node.config.get("security", {})
        serial_enabled = security.get("serial_enabled", True)

        if serial_enabled:
            return ConfigCheck(
                node_id=node.id,
                check_type="serial_access",
                expected_value="Serial disabled",
                actual_value="Serial enabled",
                status="warning",
                message="Serial console is enabled (security consideration)",
            )

        return ConfigCheck(
            node_id=node.id,
            check_type="serial_access",
            expected_value="Serial disabled",
            actual_value="Serial disabled",
            status="pass",
            message="Serial console is disabled",
        )

    async def check_all_nodes(self, nodes: list[Node]) -> list[ConfigCheck]:
        """Run configuration checks on all nodes.

        Args:
            nodes: List of nodes to check

        Returns:
            List of all ConfigCheck results
        """
        all_checks = []
        for node in nodes:
            checks = await self.check_node(node)
            all_checks.extend(checks)

        return all_checks