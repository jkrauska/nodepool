"""Tests for database operations."""

from datetime import datetime

import pytest

from nodepool.database import AsyncDatabase
from nodepool.models import ConfigCheck, ConfigSnapshot, Node


@pytest.mark.asyncio
async def test_database_initialization(db):
    """Test database initialization creates tables."""
    # Database should be initialized by the fixture
    cursor = await db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]

    assert "nodes" in tables
    assert "config_snapshots" in tables
    assert "config_checks" in tables


@pytest.mark.asyncio
async def test_save_and_get_node(db, sample_node):
    """Test saving and retrieving a node."""
    await db.save_node(sample_node)

    retrieved = await db.get_node(sample_node.id)
    assert retrieved is not None
    assert retrieved.id == sample_node.id
    assert retrieved.short_name == sample_node.short_name
    assert retrieved.long_name == sample_node.long_name
    assert retrieved.config == sample_node.config


@pytest.mark.asyncio
async def test_get_nonexistent_node(db):
    """Test getting a node that doesn't exist."""
    result = await db.get_node("!nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_update_existing_node(db, sample_node):
    """Test updating an existing node."""
    await db.save_node(sample_node)

    # Update the node
    sample_node.short_name = "UPDATED"
    sample_node.firmware_version = "2.4.0"
    await db.save_node(sample_node)

    # Retrieve and verify
    retrieved = await db.get_node(sample_node.id)
    assert retrieved.short_name == "UPDATED"
    assert retrieved.firmware_version == "2.4.0"


@pytest.mark.asyncio
async def test_get_all_nodes_active_only(db_with_nodes):
    """Test getting all active nodes."""
    nodes = await db_with_nodes.get_all_nodes(active_only=True)

    assert len(nodes) == 2  # Only 2 active nodes
    assert all(node.is_active for node in nodes)
    assert nodes[0].short_name <= nodes[1].short_name  # Should be sorted


@pytest.mark.asyncio
async def test_get_all_nodes_including_inactive(db_with_nodes):
    """Test getting all nodes including inactive."""
    nodes = await db_with_nodes.get_all_nodes(active_only=False)

    assert len(nodes) == 3  # All nodes
    assert sum(1 for node in nodes if node.is_active) == 2
    assert sum(1 for node in nodes if not node.is_active) == 1


@pytest.mark.asyncio
async def test_save_config_snapshot(db, sample_node):
    """Test saving a configuration snapshot."""
    await db.save_node(sample_node)

    snapshot = ConfigSnapshot(
        node_id=sample_node.id,
        timestamp=datetime.now(),
        config=sample_node.config,
    )

    await db.save_config_snapshot(snapshot)

    # Verify snapshot was saved
    cursor = await db._conn.execute(
        "SELECT COUNT(*) FROM config_snapshots WHERE node_id = ?",
        (sample_node.id,),
    )
    count = (await cursor.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_save_config_check(db, sample_node):
    """Test saving a configuration check result."""
    await db.save_node(sample_node)

    check = ConfigCheck(
        node_id=sample_node.id,
        check_type="ttl",
        expected_value=7,
        actual_value=7,
        status="pass",
        message="TTL is correctly set to 7",
    )

    await db.save_config_check(check)

    # Verify check was saved
    cursor = await db._conn.execute(
        "SELECT COUNT(*) FROM config_checks WHERE node_id = ?",
        (sample_node.id,),
    )
    count = (await cursor.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_get_latest_checks_for_node(db, sample_node):
    """Test retrieving latest checks for a specific node."""
    await db.save_node(sample_node)

    # Save multiple checks
    for i in range(3):
        check = ConfigCheck(
            node_id=sample_node.id,
            check_type=f"check_{i}",
            expected_value=i,
            actual_value=i,
            status="pass",
            message=f"Check {i}",
        )
        await db.save_config_check(check)

    checks = await db.get_latest_checks(node_id=sample_node.id)
    assert len(checks) == 3


@pytest.mark.asyncio
async def test_get_latest_checks_all_nodes(db_with_nodes):
    """Test retrieving latest checks for all nodes."""
    # Save checks for multiple nodes
    for node in await db_with_nodes.get_all_nodes(active_only=False):
        check = ConfigCheck(
            node_id=node.id,
            check_type="test",
            expected_value=1,
            actual_value=1,
            status="pass",
            message="Test check",
        )
        await db_with_nodes.save_config_check(check)

    checks = await db_with_nodes.get_latest_checks()
    assert len(checks) == 3


@pytest.mark.asyncio
async def test_context_manager(sample_node):
    """Test database context manager."""
    async with AsyncDatabase(":memory:") as db:
        await db.initialize()
        await db.save_node(sample_node)

        retrieved = await db.get_node(sample_node.id)
        assert retrieved is not None

    # Connection should be closed after context


@pytest.mark.asyncio
async def test_node_ordering(db):
    """Test that nodes are returned in alphabetical order."""
    nodes_data = [
        ("!c", "CHARLIE", "Charlie Node"),
        ("!a", "ALPHA", "Alpha Node"),
        ("!b", "BRAVO", "Bravo Node"),
    ]

    for node_id, short_name, long_name in nodes_data:
        node = Node(
            id=node_id,
            short_name=short_name,
            long_name=long_name,
            config={},
        )
        await db.save_node(node)

    nodes = await db.get_all_nodes()
    names = [node.short_name for node in nodes]
    assert names == ["ALPHA", "BRAVO", "CHARLIE"]
