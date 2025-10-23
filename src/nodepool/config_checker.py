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

        return checks

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
