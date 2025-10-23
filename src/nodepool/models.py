"""Data models for nodepool."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    """Represents a Meshtastic node in the pool."""

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
    )

    id: str = Field(..., description="Meshtastic node ID (e.g., !abc123)")
    short_name: str = Field(..., description="Short name of the node")
    long_name: str = Field(..., description="Long name of the node")
    serial_port: str | None = Field(None, description="Serial port path")
    hw_model: str | None = Field(None, description="Hardware model")
    firmware_version: str | None = Field(None, description="Firmware version")
    last_seen: datetime = Field(default_factory=datetime.now, description="Last seen timestamp")
    is_active: bool = Field(True, description="Whether the node is active")
    managed: bool = Field(True, description="Whether this node is directly managed (vs heard on mesh)")
    snr: float | None = Field(None, description="Signal-to-noise ratio")
    hops_away: int | None = Field(None, description="Number of hops away from managed node")
    config: dict[str, Any] = Field(default_factory=dict, description="Node configuration")


class ConfigCheck(BaseModel):
    """Represents a configuration check result."""

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
    )

    node_id: str = Field(..., description="Node ID being checked")
    check_type: str = Field(..., description="Type of check (e.g., 'ttl', 'channel')")
    expected_value: Any = Field(..., description="Expected configuration value")
    actual_value: Any = Field(..., description="Actual configuration value")
    status: str = Field(..., description="Check status: 'pass', 'fail', or 'warning'")
    message: str = Field(..., description="Human-readable check message")
    timestamp: datetime = Field(default_factory=datetime.now, description="Check timestamp")


class ConfigSnapshot(BaseModel):
    """Represents a snapshot of a node's configuration."""

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
    )

    node_id: str = Field(..., description="Node ID")
    timestamp: datetime = Field(default_factory=datetime.now, description="Snapshot timestamp")
    config: dict[str, Any] = Field(..., description="Full configuration snapshot")


class NodeStatus(BaseModel):
    """Represents the current status of a node."""

    node: Node
    reachable: bool = Field(..., description="Whether the node is currently reachable")
    last_check: datetime = Field(
        default_factory=datetime.now, description="Last reachability check"
    )
    error: str | None = Field(None, description="Error message if not reachable")


class HeardHistory(BaseModel):
    """Represents a historical record of when a node was heard by a managed node."""

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
    )

    node_id: str = Field(..., description="Node ID that was heard")
    seen_by: str = Field(..., description="Managed node ID that heard this node")
    timestamp: datetime = Field(default_factory=datetime.now, description="When the node was heard")
    snr: float | None = Field(None, description="Signal-to-noise ratio at time of hearing")
    hops_away: int | None = Field(None, description="Number of hops away")
    position_lat: float | None = Field(None, description="GPS latitude if available")
    position_lon: float | None = Field(None, description="GPS longitude if available")