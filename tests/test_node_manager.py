"""Tests for node manager."""

from unittest.mock import MagicMock, patch

import pytest

from nodepool.node_manager import NodeManager


@pytest.fixture
def mock_serial_interface():
    """Create a mock Meshtastic serial interface."""
    mock = MagicMock()
    mock.myInfo = {
        "user": {
            "id": "!abc123",
            "shortName": "TEST",
            "longName": "Test Node",
            "hwModel": "HELTEC_V3",
        }
    }

    # Mock localNode
    mock.localNode = MagicMock()
    mock.localNode.firmwareVersion = "2.3.0"

    # Mock radio config
    mock.localNode.radioConfig = MagicMock()
    mock.localNode.radioConfig.hopLimit = 7
    mock.localNode.radioConfig.region = "US"

    # Mock device config
    mock.localNode.deviceConfig = MagicMock()
    mock.localNode.deviceConfig.role = "ROUTER"

    # Mock channels
    channel1 = MagicMock()
    channel1.name = "Primary"
    channel1.index = 0
    mock.localNode.channels = [channel1]

    mock.close = MagicMock()

    return mock


@pytest.mark.asyncio
async def test_list_serial_ports():
    """Test listing serial ports."""
    manager = NodeManager()
    ports = await manager._list_serial_ports()

    # Should return a list (may be empty on test systems)
    assert isinstance(ports, list)


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_scan_port_success(mock_interface_class, mock_serial_interface):
    """Test successful port scan."""
    mock_interface_class.return_value = mock_serial_interface

    manager = NodeManager()
    node = await manager._scan_port("/dev/ttyUSB0")

    assert node.id == "!abc123"
    assert node.short_name == "TEST"
    assert node.long_name == "Test Node"
    assert node.serial_port == "/dev/ttyUSB0"
    assert node.hw_model == "HELTEC_V3"
    assert node.firmware_version == "2.3.0"
    assert node.config["lora"]["hopLimit"] == 7


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_scan_port_no_node_info(mock_interface_class):
    """Test port scan when no node info available."""
    mock = MagicMock()
    mock.myInfo = None
    mock.close = MagicMock()
    mock_interface_class.return_value = mock

    manager = NodeManager()

    with pytest.raises(Exception):
        await manager._scan_port("/dev/ttyUSB0")


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_scan_port_connection_fails(mock_interface_class):
    """Test port scan when connection fails."""
    mock_interface_class.side_effect = Exception("Connection failed")

    manager = NodeManager()

    with pytest.raises(Exception):
        await manager._scan_port("/dev/ttyUSB0")


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_discover_nodes_success(mock_interface_class, mock_serial_interface):
    """Test discovering nodes on multiple ports."""
    mock_interface_class.return_value = mock_serial_interface

    manager = NodeManager()
    nodes = await manager.discover_nodes(["/dev/ttyUSB0", "/dev/ttyUSB1"])

    # Should discover nodes (depends on mock behavior)
    assert isinstance(nodes, list)
    # Note: Both ports return same node ID, so only one is stored in dict
    assert len(nodes) == 2
    assert len(manager.discovered_nodes) == 1  # Same node ID on both ports


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_discover_nodes_mixed_results(mock_interface_class, mock_serial_interface):
    """Test discovering nodes with some failures."""
    # First port succeeds, second fails
    mock_interface_class.side_effect = [
        mock_serial_interface,
        Exception("Connection failed"),
    ]

    manager = NodeManager()
    nodes = await manager.discover_nodes(["/dev/ttyUSB0", "/dev/ttyUSB1"])

    # Should have one successful node
    assert len(nodes) == 1
    assert nodes[0].id == "!abc123"


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_check_node_reachability_success(
    mock_interface_class, mock_serial_interface, sample_node
):
    """Test checking node reachability when node is reachable."""
    mock_interface_class.return_value = mock_serial_interface

    manager = NodeManager()
    status = await manager.check_node_reachability(sample_node)

    assert status.reachable is True
    assert status.node.id == sample_node.id
    assert status.error is None


@pytest.mark.asyncio
async def test_check_node_reachability_no_port(sample_node):
    """Test checking reachability when node has no serial port."""
    sample_node.serial_port = None

    manager = NodeManager()
    status = await manager.check_node_reachability(sample_node)

    assert status.reachable is False
    assert "No serial port" in status.error


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_check_node_reachability_fails(mock_interface_class, sample_node):
    """Test checking reachability when node is not reachable."""
    mock_interface_class.side_effect = Exception("Connection failed")

    manager = NodeManager()
    status = await manager.check_node_reachability(sample_node)

    assert status.reachable is False
    assert status.error is not None


@pytest.mark.asyncio
@patch("meshtastic.serial_interface.SerialInterface")
async def test_check_all_reachability(mock_interface_class, mock_serial_interface, sample_nodes):
    """Test checking reachability of multiple nodes concurrently."""
    mock_interface_class.return_value = mock_serial_interface

    manager = NodeManager()
    statuses = await manager.check_all_reachability(sample_nodes)

    assert len(statuses) == len(sample_nodes)
    assert all(isinstance(status.reachable, bool) for status in statuses)


def test_extract_config_minimal():
    """Test config extraction with minimal interface."""
    manager = NodeManager()

    # Mock interface with no config
    mock = MagicMock()
    mock.localNode = None

    config = manager._extract_config(mock)

    assert isinstance(config, dict)
    assert len(config) == 0


def test_extract_config_full(mock_serial_interface):
    """Test config extraction with full interface."""
    manager = NodeManager()

    config = manager._extract_config(mock_serial_interface)

    assert "lora" in config
    assert config["lora"]["hopLimit"] == 7
    assert config["lora"]["region"] == "US"
    assert "device" in config
    assert config["device"]["role"] == "ROUTER"
    assert "channels" in config
    assert len(config["channels"]) == 1
