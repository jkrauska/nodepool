"""Tests for MeshView API client."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError

from nodepool.meshview_api import MeshViewAPIClient
from nodepool.models import Node


@pytest.fixture
def api_client():
    """Create a test API client."""
    return MeshViewAPIClient(base_url="https://test.example.com")


@pytest.fixture
def sample_api_response():
    """Sample API response data."""
    return [
        {
            "id": "!abc123",
            "short_name": "TestNode1",
            "long_name": "Test Node 1",
            "hw_model": "HELTEC_V3",
            "firmware_version": "2.3.0",
            "last_seen": "2024-01-15T12:00:00Z",
            "snr": 8.5,
            "hops_away": 2,
            "latitude": 37.7749,
            "longitude": -122.4194,
        },
        {
            "id": "def456",  # Test without ! prefix
            "shortName": "TestNode2",  # Test camelCase variant
            "longName": "Test Node 2",
            "hwModel": "TBEAM",
            "firmwareVersion": "2.2.0",
            "lastSeen": 1705320000,  # Unix timestamp
            "snr": 12.3,
            "hopsAway": 1,
        },
        {
            # Minimal required fields
            "id": "!xyz789",
            "short_name": "MinNode",
        },
    ]


class TestMeshViewAPIClient:
    """Tests for MeshViewAPIClient."""

    async def test_init(self):
        """Test client initialization."""
        client = MeshViewAPIClient(base_url="https://example.com/")
        assert client.base_url == "https://example.com"

        client = MeshViewAPIClient(base_url="https://example.com")
        assert client.base_url == "https://example.com"

    async def test_fetch_nodes_success(self, api_client, sample_api_response):
        """Test successful node fetching."""
        with patch("aiohttp.ClientSession.get") as mock_get:
            # Mock the response
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_response.json = AsyncMock(return_value=sample_api_response)
            mock_get.return_value.__aenter__.return_value = mock_response

            nodes, history = await api_client.fetch_nodes(days_active=3)

            # Verify API call
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert "https://test.example.com/api/nodes" in str(call_args)

            # Check nodes
            assert len(nodes) == 3
            assert all(isinstance(node, Node) for node in nodes)

            # Check first node
            node1 = nodes[0]
            assert node1.id == "!abc123"
            assert node1.short_name == "TestNode1"
            assert node1.long_name == "Test Node 1"
            assert node1.hw_model == "HELTEC_V3"
            assert node1.firmware_version == "2.3.0"
            assert node1.snr == 8.5
            assert node1.hops_away == 2
            assert node1.managed is False
            assert node1.serial_port is None

            # Check second node (camelCase and Unix timestamp)
            node2 = nodes[1]
            assert node2.id == "!def456"  # Should add ! prefix
            assert node2.short_name == "TestNode2"
            assert node2.hw_model == "TBEAM"

            # Check third node (minimal fields)
            node3 = nodes[2]
            assert node3.id == "!xyz789"
            assert node3.short_name == "MinNode"
            assert node3.long_name == "MinNode"  # Defaults to short_name

            # Check heard history
            assert len(history) == 3
            assert all(h.seen_by == "meshviewAPI" for h in history)
            assert history[0].node_id == "!abc123"
            assert history[0].snr == 8.5
            assert history[0].hops_away == 2
            assert history[0].position_lat == 37.7749
            assert history[0].position_lon == -122.4194

    async def test_fetch_nodes_empty_response(self, api_client):
        """Test handling of empty API response."""
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_response.json = AsyncMock(return_value=[])
            mock_get.return_value.__aenter__.return_value = mock_response

            nodes, history = await api_client.fetch_nodes(days_active=3)

            assert nodes == []
            assert history == []

    async def test_fetch_nodes_invalid_response_type(self, api_client):
        """Test handling of invalid response type."""
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_response.json = AsyncMock(return_value={"error": "not a list"})
            mock_get.return_value.__aenter__.return_value = mock_response

            with pytest.raises(ValueError, match="Expected list of nodes"):
                await api_client.fetch_nodes(days_active=3)

    async def test_fetch_nodes_network_error(self, api_client):
        """Test handling of network errors."""
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_get.side_effect = ClientError("Network error")

            with pytest.raises(ClientError):
                await api_client.fetch_nodes(days_active=3)

    async def test_fetch_nodes_skip_invalid_entries(self, api_client):
        """Test that invalid entries are skipped."""
        invalid_data = [
            {"id": "!valid123", "short_name": "ValidNode"},
            {"short_name": "NoID"},  # Missing ID
            {"id": "!another", "short_name": "AnotherValid"},
        ]

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_response.json = AsyncMock(return_value=invalid_data)
            mock_get.return_value.__aenter__.return_value = mock_response

            nodes, history = await api_client.fetch_nodes(days_active=3)

            # Should only get the valid nodes
            assert len(nodes) == 2
            assert nodes[0].id == "!valid123"
            assert nodes[1].id == "!another"

    async def test_parse_node_timestamp_formats(self, api_client):
        """Test parsing different timestamp formats."""
        now = datetime.now(timezone.utc)

        # Test ISO format with Z
        data1 = {
            "id": "!test1",
            "short_name": "Test1",
            "last_seen": "2024-01-15T12:00:00Z",
        }
        node1 = api_client._parse_node(data1, now)
        assert isinstance(node1.last_seen, datetime)

        # Test ISO format with timezone
        data2 = {
            "id": "!test2",
            "short_name": "Test2",
            "last_seen": "2024-01-15T12:00:00+00:00",
        }
        node2 = api_client._parse_node(data2, now)
        assert isinstance(node2.last_seen, datetime)

        # Test Unix timestamp
        data3 = {
            "id": "!test3",
            "short_name": "Test3",
            "last_seen": 1705320000,
        }
        node3 = api_client._parse_node(data3, now)
        assert isinstance(node3.last_seen, datetime)

        # Test missing timestamp (should use fetch_time)
        data4 = {"id": "!test4", "short_name": "Test4"}
        node4 = api_client._parse_node(data4, now)
        assert node4.last_seen == now

    async def test_parse_node_id_variants(self, api_client):
        """Test parsing different node ID field names."""
        now = datetime.now(timezone.utc)

        # Test 'id' field
        data1 = {"id": "!test1", "short_name": "Test1"}
        node1 = api_client._parse_node(data1, now)
        assert node1.id == "!test1"

        # Test 'node_id' field
        data2 = {"node_id": "!test2", "short_name": "Test2"}
        node2 = api_client._parse_node(data2, now)
        assert node2.id == "!test2"

        # Test adding ! prefix
        data3 = {"id": "test3", "short_name": "Test3"}
        node3 = api_client._parse_node(data3, now)
        assert node3.id == "!test3"

    async def test_parse_node_missing_id(self, api_client):
        """Test error when node ID is missing."""
        now = datetime.now(timezone.utc)
        data = {"short_name": "NoID"}

        with pytest.raises(KeyError, match="Missing node ID"):
            api_client._parse_node(data, now)

    async def test_parse_node_optional_fields(self, api_client):
        """Test handling of optional fields."""
        now = datetime.now(timezone.utc)

        # Test with only required fields
        data = {"id": "!minimal", "short_name": "Minimal"}
        node = api_client._parse_node(data, now)

        assert node.id == "!minimal"
        assert node.short_name == "Minimal"
        assert node.long_name == "Minimal"
        assert node.hw_model is None
        assert node.firmware_version is None
        assert node.snr is None
        assert node.hops_away is None
        assert node.config == {}
        assert node.managed is False

    async def test_parse_node_invalid_snr(self, api_client):
        """Test handling of invalid SNR values."""
        now = datetime.now(timezone.utc)

        data = {
            "id": "!test",
            "short_name": "Test",
            "snr": "invalid",
        }
        node = api_client._parse_node(data, now)
        assert node.snr is None

    async def test_parse_node_invalid_hops(self, api_client):
        """Test handling of invalid hops_away values."""
        now = datetime.now(timezone.utc)

        data = {
            "id": "!test",
            "short_name": "Test",
            "hops_away": "invalid",
        }
        node = api_client._parse_node(data, now)
        assert node.hops_away is None

    async def test_custom_base_url(self):
        """Test using a custom base URL."""
        client = MeshViewAPIClient(base_url="https://custom.example.com")
        assert client.base_url == "https://custom.example.com"

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_response.json = AsyncMock(return_value=[])
            mock_get.return_value.__aenter__.return_value = mock_response

            await client.fetch_nodes(days_active=7)

            call_args = mock_get.call_args
            assert "https://custom.example.com/api/nodes" in str(call_args)
