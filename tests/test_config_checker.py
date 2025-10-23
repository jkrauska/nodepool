"""Tests for configuration checker."""

import pytest

from nodepool.config_checker import ConfigChecker
from nodepool.models import Node


@pytest.mark.asyncio
async def test_check_ttl_pass(sample_node):
    """Test TTL check passes when value matches."""
    checker = ConfigChecker(expected_ttl=7)
    result = await checker.check_ttl(sample_node)

    assert result.status == "pass"
    assert result.check_type == "ttl"
    assert result.expected_value == 7
    assert result.actual_value == 7


@pytest.mark.asyncio
async def test_check_ttl_fail():
    """Test TTL check fails when value doesn't match."""
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={"lora": {"hopLimit": 3}},
    )

    checker = ConfigChecker(expected_ttl=7)
    result = await checker.check_ttl(node)

    assert result.status == "fail"
    assert result.expected_value == 7
    assert result.actual_value == 3


@pytest.mark.asyncio
async def test_check_ttl_warning_missing():
    """Test TTL check warns when value is missing."""
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={},
    )

    checker = ConfigChecker(expected_ttl=7)
    result = await checker.check_ttl(node)

    assert result.status == "warning"
    assert result.actual_value is None


@pytest.mark.asyncio
async def test_check_region_pass(sample_node):
    """Test region check passes when value matches."""
    checker = ConfigChecker(expected_region="US")
    result = await checker.check_region(sample_node)

    assert result.status == "pass"
    assert result.check_type == "region"
    assert result.expected_value == "US"
    assert result.actual_value == "US"


@pytest.mark.asyncio
async def test_check_region_fail():
    """Test region check fails when value doesn't match."""
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={"lora": {"region": "EU_868"}},
    )

    checker = ConfigChecker(expected_region="US")
    result = await checker.check_region(node)

    assert result.status == "fail"
    assert result.expected_value == "US"
    assert result.actual_value == "EU_868"


@pytest.mark.asyncio
async def test_check_region_skipped_when_not_configured():
    """Test region check is skipped when no expected region."""
    checker = ConfigChecker(expected_region=None)
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={"lora": {"region": "US"}},
    )

    result = await checker.check_region(node)

    assert result.status == "pass"
    assert "skipped" in result.message.lower()


@pytest.mark.asyncio
async def test_check_channel_present():
    """Test channel check when channel is present."""
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={
            "channels": [
                {"name": "Primary", "index": 0},
                {"name": "Secondary", "index": 1},
            ]
        },
    )

    checker = ConfigChecker(expected_channels=[{"name": "Secondary"}])
    result = await checker.check_channel(node, channel_index=1)

    assert result.status == "pass"
    assert "configured" in result.message.lower()


@pytest.mark.asyncio
async def test_check_channel_missing():
    """Test channel check when channel is missing."""
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={"channels": [{"name": "Primary", "index": 0}]},
    )

    checker = ConfigChecker(expected_channels=[{"name": "Secondary"}])
    result = await checker.check_channel(node, channel_index=1)

    assert result.status == "warning"
    assert "not configured" in result.message.lower()


@pytest.mark.asyncio
async def test_check_node_all_checks(sample_node):
    """Test running all checks on a node."""
    checker = ConfigChecker(expected_ttl=7, expected_region="US")
    results = await checker.check_node(sample_node)

    assert len(results) >= 2  # At least TTL and region
    assert any(r.check_type == "ttl" for r in results)
    assert any(r.check_type == "region" for r in results)


@pytest.mark.asyncio
async def test_check_all_nodes(sample_nodes):
    """Test running checks on multiple nodes."""
    checker = ConfigChecker(expected_ttl=7, expected_region="US")
    results = await checker.check_all_nodes(sample_nodes)

    # Should have checks for all nodes
    assert len(results) >= len(sample_nodes) * 2  # At least TTL and region per node

    # Check that we have results for each node
    node_ids = {r.node_id for r in results}
    assert len(node_ids) == len(sample_nodes)


@pytest.mark.asyncio
async def test_check_with_custom_ttl():
    """Test configuration checker with custom TTL."""
    node = Node(
        id="!test",
        short_name="TEST",
        long_name="Test Node",
        config={"lora": {"hopLimit": 5}},
    )

    checker = ConfigChecker(expected_ttl=5)
    result = await checker.check_ttl(node)

    assert result.status == "pass"
    assert result.expected_value == 5


@pytest.mark.asyncio
async def test_mixed_check_results(sample_nodes):
    """Test that checks properly identify pass/fail/warning."""
    checker = ConfigChecker(expected_ttl=7, expected_region="US")
    results = await checker.check_all_nodes(sample_nodes)

    # Should have passing checks
    assert any(r.status == "pass" for r in results)

    # Should have failing checks (NODE2 has TTL=3)
    assert any(r.status == "fail" for r in results)

    # Check NODE2 specifically fails TTL
    node2_ttl_checks = [r for r in results if r.node_id == "!def456" and r.check_type == "ttl"]
    assert len(node2_ttl_checks) == 1
    assert node2_ttl_checks[0].status == "fail"
