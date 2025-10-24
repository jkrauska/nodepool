"""MeshView API client for fetching node data."""

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp
from rich.console import Console

from nodepool.models import HeardHistory, Node

console = Console()


class MeshViewAPIClient:
    """Client for fetching node data from MeshView API."""

    def __init__(self, base_url: str = "https://meshview.bayme.sh"):
        """Initialize MeshView API client.

        Args:
            base_url: Base URL for the MeshView API
        """
        self.base_url = base_url.rstrip("/")

    async def fetch_nodes(self, days_active: int = 3) -> tuple[list[Node], list[HeardHistory]]:
        """Fetch nodes from the MeshView API.

        Args:
            days_active: Number of days of activity to filter by

        Returns:
            Tuple of (list of Node objects, list of HeardHistory objects)

        Raises:
            aiohttp.ClientError: If the API request fails
            ValueError: If the API response is invalid
        """
        url = f"{self.base_url}/api/nodes"
        params = {"days_active": days_active}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

        # Handle both direct list and {"nodes": [...]} format
        if isinstance(data, dict):
            if "nodes" in data:
                node_list = data["nodes"]
            else:
                raise ValueError(f"Expected 'nodes' key in response, got keys: {list(data.keys())}")
        elif isinstance(data, list):
            node_list = data
        else:
            raise ValueError(f"Expected list or dict with 'nodes', got {type(data)}")

        nodes = []
        heard_history = []
        now = datetime.now(timezone.utc)

        for node_data in node_list:
            try:
                node = self._parse_node(node_data, now)
                nodes.append(node)

                # Create heard history entry
                # Convert position from microdegrees if present
                lat = node_data.get("last_lat") or node_data.get("latitude")
                lon = node_data.get("last_long") or node_data.get("longitude")
                
                # Convert from microdegrees to degrees if needed (values > 180 are likely microdegrees)
                if lat is not None and abs(lat) > 180:
                    lat = lat / 1e7
                if lon is not None and abs(lon) > 180:
                    lon = lon / 1e7
                
                history = HeardHistory(
                    node_id=node.id,
                    long_name=node.long_name,
                    seen_by="meshviewAPI",
                    timestamp=node.last_seen,
                    snr=node.snr,
                    hops_away=node.hops_away,
                    position_lat=lat,
                    position_lon=lon,
                )
                heard_history.append(history)

            except (KeyError, ValueError) as e:
                console.print(f"[yellow]Warning: Skipping invalid node data: {e}[/yellow]")
                continue

        return nodes, heard_history

    def _parse_node(self, data: dict[str, Any], fetch_time: datetime) -> Node:
        """Parse node data from API response.

        Args:
            data: Node data from API
            fetch_time: Time the data was fetched

        Returns:
            Node object

        Raises:
            KeyError: If required fields are missing
            ValueError: If data is invalid
        """
        # Extract required fields
        node_id = data.get("id") or data.get("node_id")
        if not node_id:
            raise KeyError("Missing node ID")

        # Ensure node_id starts with !
        if not node_id.startswith("!"):
            node_id = f"!{node_id}"

        short_name = data.get("short_name") or data.get("shortName") or "Unknown"
        long_name = data.get("long_name") or data.get("longName") or short_name

        # Parse timestamp (try multiple field names)
        last_seen_str = data.get("last_update") or data.get("last_seen") or data.get("lastSeen")
        if last_seen_str:
            try:
                # Try parsing ISO format
                last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                # Try parsing Unix timestamp
                try:
                    last_seen = datetime.fromtimestamp(float(last_seen_str), tz=timezone.utc)
                except (ValueError, TypeError):
                    last_seen = fetch_time
        else:
            last_seen = fetch_time

        # Extract optional fields
        hw_model = data.get("hw_model") or data.get("hwModel")
        firmware_version = data.get("firmware") or data.get("firmware_version") or data.get("firmwareVersion")
        snr = data.get("snr")
        hops_away = data.get("hops_away") or data.get("hopsAway")

        # Convert SNR to float if present
        if snr is not None:
            try:
                snr = float(snr)
            except (ValueError, TypeError):
                snr = None

        # Convert hops to int if present
        if hops_away is not None:
            try:
                hops_away = int(hops_away)
            except (ValueError, TypeError):
                hops_away = None

        return Node(
            id=node_id,
            short_name=short_name,
            long_name=long_name,
            hw_model=hw_model,
            firmware_version=firmware_version,
            first_seen=last_seen,  # Use last_seen as first_seen for API nodes
            last_seen=last_seen,
            is_active=True,
            snr=snr,
            hops_away=hops_away,
            config={},  # No config data from API
        )