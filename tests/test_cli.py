"""Tests for CLI commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from nodepool.cli import cli


@pytest.fixture
def runner():
    """Create CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_db(sample_nodes):
    """Create mock database."""
    db = AsyncMock()
    db.initialize = AsyncMock()
    db.get_all_nodes = AsyncMock(return_value=sample_nodes)
    db.get_node = AsyncMock(return_value=sample_nodes[0])
    db.save_node = AsyncMock()
    db.save_config_check = AsyncMock()
    return db


def test_cli_version(runner):
    """Test CLI version command."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_help(runner):
    """Test CLI help command."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Nodepool" in result.output
    assert "discover" in result.output
    assert "list" in result.output
    assert "check" in result.output


@patch("nodepool.cli.NodeManager")
@patch("nodepool.cli.AsyncDatabase")
def test_discover_command(mock_db_class, mock_manager_class, runner, sample_node):
    """Test discover command."""
    # Setup mocks
    mock_manager = MagicMock()
    mock_manager._list_serial_ports = AsyncMock(return_value=["/dev/ttyUSB0"])
    mock_manager.discover_nodes = AsyncMock(return_value=[sample_node])
    mock_manager_class.return_value = mock_manager

    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.save_node = AsyncMock()
    mock_db_class.return_value = mock_db

    result = runner.invoke(cli, ["discover"])

    assert result.exit_code == 0
    assert "Discovering" in result.output


@patch("nodepool.cli.AsyncDatabase")
def test_list_command(mock_db_class, runner, sample_nodes):
    """Test list command."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_all_nodes = AsyncMock(return_value=sample_nodes)
    mock_db_class.return_value = mock_db

    result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0
    assert "NODE1" in result.output
    assert "NODE2" in result.output


@patch("nodepool.cli.AsyncDatabase")
def test_list_command_empty(mock_db_class, runner):
    """Test list command with empty database."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_all_nodes = AsyncMock(return_value=[])
    mock_db_class.return_value = mock_db

    result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0
    assert "No nodes found" in result.output


@patch("nodepool.cli.AsyncDatabase")
def test_info_command(mock_db_class, runner, sample_node):
    """Test info command."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_node = AsyncMock(return_value=sample_node)
    mock_db_class.return_value = mock_db

    result = runner.invoke(cli, ["info", sample_node.id])

    assert result.exit_code == 0
    assert sample_node.short_name in result.output
    assert sample_node.id in result.output


@patch("nodepool.cli.AsyncDatabase")
def test_info_command_not_found(mock_db_class, runner):
    """Test info command with non-existent node."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_node = AsyncMock(return_value=None)
    mock_db_class.return_value = mock_db

    result = runner.invoke(cli, ["info", "!nonexistent"])

    assert result.exit_code == 0
    assert "not found" in result.output


@patch("nodepool.cli.ConfigChecker")
@patch("nodepool.cli.AsyncDatabase")
def test_check_command(mock_db_class, mock_checker_class, runner, sample_nodes, sample_node):
    """Test check command."""
    from nodepool.models import ConfigCheck

    # Setup database mock
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_all_nodes = AsyncMock(return_value=sample_nodes)
    mock_db.save_config_check = AsyncMock()
    mock_db_class.return_value = mock_db

    # Setup checker mock
    mock_checker = MagicMock()
    check_result = ConfigCheck(
        node_id=sample_node.id,
        check_type="ttl",
        expected_value=7,
        actual_value=7,
        status="pass",
        message="TTL correctly set",
    )
    mock_checker.check_all_nodes = AsyncMock(return_value=[check_result])
    mock_checker_class.return_value = mock_checker

    result = runner.invoke(cli, ["check"])

    assert result.exit_code == 0
    assert "Running configuration checks" in result.output


@patch("nodepool.cli.NodeManager")
@patch("nodepool.cli.AsyncDatabase")
def test_status_command(mock_db_class, mock_manager_class, runner, sample_nodes):
    """Test status command."""
    from nodepool.models import NodeStatus

    # Setup database mock
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_all_nodes = AsyncMock(return_value=sample_nodes)
    mock_db_class.return_value = mock_db

    # Setup manager mock
    mock_manager = MagicMock()
    statuses = [
        NodeStatus(node=node, reachable=True, error=None) for node in sample_nodes
    ]
    mock_manager.check_all_reachability = AsyncMock(return_value=statuses)
    mock_manager_class.return_value = mock_manager

    result = runner.invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Checking status" in result.output


@patch("nodepool.cli.AsyncDatabase")
def test_export_command_json(mock_db_class, runner, sample_nodes):
    """Test export command with JSON format."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_all_nodes = AsyncMock(return_value=sample_nodes)
    mock_db_class.return_value = mock_db

    result = runner.invoke(cli, ["export"])

    assert result.exit_code == 0
    assert '"id"' in result.output  # JSON format
    assert sample_nodes[0].id in result.output


@patch("nodepool.cli.AsyncDatabase")
def test_export_command_to_file(mock_db_class, runner, sample_nodes, tmp_path):
    """Test export command with output file."""
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mock_db.initialize = AsyncMock()
    mock_db.get_all_nodes = AsyncMock(return_value=sample_nodes)
    mock_db_class.return_value = mock_db

    output_file = tmp_path / "export.json"
    result = runner.invoke(cli, ["export", "-o", str(output_file)])

    assert result.exit_code == 0
    assert output_file.exists()
    assert "Exported" in result.output