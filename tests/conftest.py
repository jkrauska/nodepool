"""Shared test fixtures."""

from datetime import datetime

import pytest

from nodepool.database import AsyncDatabase
from nodepool.models import Node


@pytest.fixture
async def db():
    """Create in-memory test database."""
    database = AsyncDatabase(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def sample_node():
    """Create a sample node for testing."""
    return Node(
        id="!abc123",
        short_name="NODE1",
        long_name="Test Node 1",
        serial_port="/dev/ttyUSB0",
        hw_model="HELTEC_V3",
        firmware_version="2.3.0",
        last_seen=datetime(2025, 1, 1, 12, 0, 0),
        is_active=True,
        config={
            "lora": {"hopLimit": 7, "region": "US"},
            "device": {"role": "ROUTER"},
        },
    )


@pytest.fixture
def sample_nodes():
    """Create multiple sample nodes for testing."""
    return [
        Node(
            id="!abc123",
            short_name="NODE1",
            long_name="Test Node 1",
            serial_port="/dev/ttyUSB0",
            hw_model="HELTEC_V3",
            firmware_version="2.3.0",
            is_active=True,
            config={
                "lora": {"hopLimit": 7, "region": "US"},
                "device": {"role": "ROUTER"},
            },
        ),
        Node(
            id="!def456",
            short_name="NODE2",
            long_name="Test Node 2",
            serial_port="/dev/ttyUSB1",
            hw_model="TLORA_V2_1_1P6",
            firmware_version="2.3.0",
            is_active=True,
            config={
                "lora": {"hopLimit": 3, "region": "US"},
                "device": {"role": "CLIENT"},
            },
        ),
        Node(
            id="!ghi789",
            short_name="NODE3",
            long_name="Test Node 3",
            serial_port="/dev/ttyUSB2",
            hw_model="TBEAM",
            firmware_version="2.2.0",
            is_active=False,
            config={
                "lora": {"hopLimit": 7, "region": "EU_868"},
                "device": {"role": "CLIENT"},
            },
        ),
    ]


@pytest.fixture
async def db_with_nodes(db, sample_nodes):
    """Create database with sample nodes."""
    for node in sample_nodes:
        await db.save_node(node)
    return db
