"""Database operations for nodepool."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from nodepool.models import ConfigCheck, ConfigSnapshot, HeardHistory, Node


class AsyncDatabase:
    """Async SQLite database for storing node information."""

    def __init__(self, db_path: str | Path = "nodepool.db"):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Connect to the database."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def initialize(self) -> None:
        """Initialize database schema."""
        if not self._conn:
            await self.connect()

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                short_name TEXT NOT NULL,
                long_name TEXT NOT NULL,
                serial_port TEXT,
                hw_model TEXT,
                firmware_version TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                managed INTEGER NOT NULL DEFAULT 1,
                snr REAL,
                hops_away INTEGER,
                config TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS config_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                config TEXT NOT NULL,
                FOREIGN KEY (node_id) REFERENCES nodes(id)
            );

            CREATE TABLE IF NOT EXISTS config_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                check_type TEXT NOT NULL,
                expected_value TEXT,
                actual_value TEXT,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                FOREIGN KEY (node_id) REFERENCES nodes(id)
            );

            CREATE TABLE IF NOT EXISTS heard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                seen_by TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                snr REAL,
                hops_away INTEGER,
                position_lat REAL,
                position_lon REAL,
                FOREIGN KEY (node_id) REFERENCES nodes(id),
                FOREIGN KEY (seen_by) REFERENCES nodes(id)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_active ON nodes(is_active);
            CREATE INDEX IF NOT EXISTS idx_nodes_managed ON nodes(managed);
            CREATE INDEX IF NOT EXISTS idx_snapshots_node ON config_snapshots(node_id);
            CREATE INDEX IF NOT EXISTS idx_checks_node ON config_checks(node_id);
            CREATE INDEX IF NOT EXISTS idx_checks_timestamp ON config_checks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_heard_node ON heard_history(node_id);
            CREATE INDEX IF NOT EXISTS idx_heard_by ON heard_history(seen_by);
            CREATE INDEX IF NOT EXISTS idx_heard_time ON heard_history(timestamp);
            """
        )
        await self._conn.commit()

        # Migrate existing nodes table if needed
        await self._migrate_schema()

    async def _migrate_schema(self) -> None:
        """Migrate existing database schema to add new columns."""
        if not self._conn:
            return

        # Check if managed column exists
        cursor = await self._conn.execute("PRAGMA table_info(nodes)")
        columns = await cursor.fetchall()
        column_names = [col["name"] for col in columns]

        # Add missing columns
        if "managed" not in column_names:
            await self._conn.execute("ALTER TABLE nodes ADD COLUMN managed INTEGER NOT NULL DEFAULT 1")
        if "snr" not in column_names:
            await self._conn.execute("ALTER TABLE nodes ADD COLUMN snr REAL")
        if "hops_away" not in column_names:
            await self._conn.execute("ALTER TABLE nodes ADD COLUMN hops_away INTEGER")
        if "first_seen" not in column_names:
            # Add first_seen column and backfill with last_seen values
            await self._conn.execute("ALTER TABLE nodes ADD COLUMN first_seen TEXT")
            await self._conn.execute("UPDATE nodes SET first_seen = last_seen WHERE first_seen IS NULL")

        await self._conn.commit()

    async def save_node(self, node: Node) -> None:
        """Save or update a node in the database.

        Args:
            node: Node object to save
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            """
            INSERT INTO nodes (
                id, short_name, long_name, serial_port, hw_model,
                firmware_version, first_seen, last_seen, is_active, managed,
                snr, hops_away, config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                short_name = excluded.short_name,
                long_name = excluded.long_name,
                serial_port = excluded.serial_port,
                hw_model = excluded.hw_model,
                firmware_version = excluded.firmware_version,
                last_seen = excluded.last_seen,
                is_active = excluded.is_active,
                managed = excluded.managed,
                snr = excluded.snr,
                hops_away = excluded.hops_away,
                config = excluded.config
            """,
            (
                node.id,
                node.short_name,
                node.long_name,
                node.serial_port,
                node.hw_model,
                node.firmware_version,
                node.first_seen.isoformat(),
                node.last_seen.isoformat(),
                1 if node.is_active else 0,
                1 if node.managed else 0,
                node.snr,
                node.hops_away,
                json.dumps(node.config),
            ),
        )
        await self._conn.commit()

    async def get_node(self, node_id: str) -> Node | None:
        """Get a node by ID.

        Args:
            node_id: Node ID to retrieve

        Returns:
            Node object or None if not found
        """
        if not self._conn:
            await self.connect()

        cursor = await self._conn.execute(
            "SELECT * FROM nodes WHERE id = ?",
            (node_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return self._row_to_node(row)

    async def get_all_nodes(self, active_only: bool = True) -> list[Node]:
        """Get all nodes from the database.

        Args:
            active_only: If True, only return active nodes

        Returns:
            List of Node objects
        """
        if not self._conn:
            await self.connect()

        query = "SELECT * FROM nodes"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY short_name"

        cursor = await self._conn.execute(query)
        rows = await cursor.fetchall()

        return [self._row_to_node(row) for row in rows]

    async def save_config_snapshot(self, snapshot: ConfigSnapshot) -> None:
        """Save a configuration snapshot.

        Args:
            snapshot: ConfigSnapshot object to save
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            """
            INSERT INTO config_snapshots (node_id, timestamp, config)
            VALUES (?, ?, ?)
            """,
            (
                snapshot.node_id,
                snapshot.timestamp.isoformat(),
                json.dumps(snapshot.config),
            ),
        )
        await self._conn.commit()

    async def save_config_check(self, check: ConfigCheck) -> None:
        """Save a configuration check result.

        Args:
            check: ConfigCheck object to save
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            """
            INSERT INTO config_checks (
                node_id, timestamp, check_type, expected_value,
                actual_value, status, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                check.node_id,
                check.timestamp.isoformat(),
                check.check_type,
                json.dumps(check.expected_value),
                json.dumps(check.actual_value),
                check.status,
                check.message,
            ),
        )
        await self._conn.commit()

    async def get_latest_checks(self, node_id: str | None = None) -> list[ConfigCheck]:
        """Get the latest configuration checks.

        Args:
            node_id: Optional node ID to filter by

        Returns:
            List of ConfigCheck objects
        """
        if not self._conn:
            await self.connect()

        if node_id:
            query = """
                SELECT * FROM config_checks
                WHERE node_id = ?
                ORDER BY timestamp DESC
                LIMIT 100
            """
            cursor = await self._conn.execute(query, (node_id,))
        else:
            query = """
                SELECT * FROM config_checks
                ORDER BY timestamp DESC
                LIMIT 100
            """
            cursor = await self._conn.execute(query)

        rows = await cursor.fetchall()
        return [self._row_to_check(row) for row in rows]

    async def save_heard_history(self, history: HeardHistory) -> None:
        """Save a heard history entry.

        Args:
            history: HeardHistory object to save
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            """
            INSERT INTO heard_history (
                node_id, seen_by, timestamp, snr, hops_away,
                position_lat, position_lon
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history.node_id,
                history.seen_by,
                history.timestamp.isoformat(),
                history.snr,
                history.hops_away,
                history.position_lat,
                history.position_lon,
            ),
        )
        await self._conn.commit()

    async def get_heard_nodes(
        self,
        seen_by: str | None = None,
        managed_only: bool = False,
    ) -> list[Node]:
        """Get nodes that have been heard on the mesh.

        Args:
            seen_by: Optional filter by which managed node heard them
            managed_only: If True, only return nodes marked as not managed

        Returns:
            List of Node objects
        """
        if not self._conn:
            await self.connect()

        query = "SELECT * FROM nodes WHERE managed = 0"
        params = []

        if seen_by:
            # Add filter based on heard_history
            query = """
                SELECT DISTINCT n.* FROM nodes n
                JOIN heard_history h ON n.id = h.node_id
                WHERE n.managed = 0 AND h.seen_by = ?
            """
            params.append(seen_by)

        query += " ORDER BY last_seen DESC"

        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()

        return [self._row_to_node(row) for row in rows]

    def _row_to_node(self, row: aiosqlite.Row) -> Node:
        """Convert database row to Node object.

        Args:
            row: Database row

        Returns:
            Node object
        """
        # Handle both old and new schema
        try:
            managed = bool(row["managed"])
        except (KeyError, IndexError):
            managed = True  # Default to managed=True for old records

        try:
            snr = row["snr"]
        except (KeyError, IndexError):
            snr = None

        try:
            hops_away = row["hops_away"]
        except (KeyError, IndexError):
            hops_away = None

        try:
            first_seen = datetime.fromisoformat(row["first_seen"])
        except (KeyError, IndexError):
            # For old records without first_seen, use last_seen as fallback
            first_seen = datetime.fromisoformat(row["last_seen"])

        return Node(
            id=row["id"],
            short_name=row["short_name"],
            long_name=row["long_name"],
            serial_port=row["serial_port"],
            hw_model=row["hw_model"],
            firmware_version=row["firmware_version"],
            first_seen=first_seen,
            last_seen=datetime.fromisoformat(row["last_seen"]),
            is_active=bool(row["is_active"]),
            managed=managed,
            snr=snr,
            hops_away=hops_away,
            config=json.loads(row["config"]),
        )

    def _row_to_check(self, row: aiosqlite.Row) -> ConfigCheck:
        """Convert database row to ConfigCheck object.

        Args:
            row: Database row

        Returns:
            ConfigCheck object
        """
        return ConfigCheck(
            node_id=row["node_id"],
            check_type=row["check_type"],
            expected_value=json.loads(row["expected_value"]),
            actual_value=json.loads(row["actual_value"]),
            status=row["status"],
            message=row["message"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    async def __aenter__(self) -> "AsyncDatabase":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
