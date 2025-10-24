"""Database operations for nodepool."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from nodepool.models import ConfigCheck, ConfigSnapshot, HeardHistory, Node, Pool, PoolMembership


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
            CREATE TABLE IF NOT EXISTS pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                short_name TEXT NOT NULL,
                long_name TEXT NOT NULL,
                hw_model TEXT,
                firmware_version TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                snr REAL,
                hops_away INTEGER,
                config TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connections (
                node_id TEXT PRIMARY KEY,
                connection_string TEXT NOT NULL,
                connected_at TEXT NOT NULL,
                FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pool_memberships (
                pool_id INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE,
                FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
                PRIMARY KEY (pool_id, node_id)
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
                long_name TEXT NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_pool_memberships_pool ON pool_memberships(pool_id);
            CREATE INDEX IF NOT EXISTS idx_pool_memberships_node ON pool_memberships(node_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_node ON config_snapshots(node_id);
            CREATE INDEX IF NOT EXISTS idx_checks_node ON config_checks(node_id);
            CREATE INDEX IF NOT EXISTS idx_checks_timestamp ON config_checks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_heard_node ON heard_history(node_id);
            CREATE INDEX IF NOT EXISTS idx_heard_by ON heard_history(seen_by);
            CREATE INDEX IF NOT EXISTS idx_heard_time ON heard_history(timestamp);
            """
        )
        await self._conn.commit()

        # Ensure default pool exists
        await self._ensure_default_pool()

    async def _ensure_default_pool(self) -> None:
        """Ensure the default pool exists."""
        if not self._conn:
            return

        cursor = await self._conn.execute(
            "SELECT id FROM pools WHERE is_default = 1"
        )
        existing = await cursor.fetchone()

        if not existing:
            await self._conn.execute(
                """
                INSERT INTO pools (name, description, is_default, created_at)
                VALUES (?, ?, 1, ?)
                """,
                ("default", "Default pool for managed nodes", datetime.now().isoformat()),
            )
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
                id, short_name, long_name, hw_model,
                firmware_version, first_seen, last_seen, is_active,
                snr, hops_away, config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                short_name = excluded.short_name,
                long_name = excluded.long_name,
                hw_model = CASE 
                    WHEN excluded.hw_model IS NOT NULL THEN excluded.hw_model
                    ELSE nodes.hw_model
                END,
                firmware_version = CASE 
                    WHEN excluded.firmware_version IS NOT NULL THEN excluded.firmware_version
                    ELSE nodes.firmware_version
                END,
                last_seen = excluded.last_seen,
                is_active = excluded.is_active,
                snr = excluded.snr,
                hops_away = excluded.hops_away,
                config = CASE
                    WHEN excluded.config != '{}' THEN excluded.config
                    ELSE nodes.config
                END
            """,
            (
                node.id,
                node.short_name,
                node.long_name,
                node.hw_model,
                node.firmware_version,
                node.first_seen.isoformat(),
                node.last_seen.isoformat(),
                1 if node.is_active else 0,
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
                node_id, long_name, seen_by, timestamp, snr, hops_away,
                position_lat, position_lon
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history.node_id,
                history.long_name,
                history.seen_by,
                history.timestamp.isoformat(),
                history.snr,
                history.hops_away,
                history.position_lat,
                history.position_lon,
            ),
        )
        await self._conn.commit()

    async def get_default_pool(self) -> Pool:
        """Get the default pool.

        Returns:
            Pool object for the default pool
        """
        if not self._conn:
            await self.connect()

        cursor = await self._conn.execute(
            "SELECT * FROM pools WHERE is_default = 1"
        )
        row = await cursor.fetchone()

        if not row:
            raise ValueError("Default pool not found")

        return self._row_to_pool(row)

    async def get_pool_by_name(self, name: str) -> Pool | None:
        """Get a pool by name.

        Args:
            name: Pool name

        Returns:
            Pool object or None if not found
        """
        if not self._conn:
            await self.connect()

        cursor = await self._conn.execute(
            "SELECT * FROM pools WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return self._row_to_pool(row)

    async def save_connection(self, node_id: str, connection_string: str) -> None:
        """Save a node connection.

        Args:
            node_id: Node ID
            connection_string: Connection string (e.g., /dev/cu.usbmodem123, tcp://192.168.1.100:4403)
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            """
            INSERT INTO connections (node_id, connection_string, connected_at)
            VALUES (?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                connection_string = excluded.connection_string,
                connected_at = excluded.connected_at
            """,
            (node_id, connection_string, datetime.now().isoformat()),
        )
        await self._conn.commit()

    async def remove_connection(self, node_id: str) -> None:
        """Remove a connection for a node.

        Args:
            node_id: Node ID
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            "DELETE FROM connections WHERE node_id = ?",
            (node_id,),
        )
        await self._conn.commit()

    async def get_connected_nodes(self) -> list[tuple[Node, str]]:
        """Get all connected nodes with their connection strings.

        Returns:
            List of tuples (Node, connection_string)
        """
        if not self._conn:
            await self.connect()

        query = """
            SELECT n.*, c.connection_string
            FROM nodes n
            JOIN connections c ON n.id = c.node_id
            WHERE n.is_active = 1
            ORDER BY n.short_name
        """

        cursor = await self._conn.execute(query)
        rows = await cursor.fetchall()

        result = []
        for row in rows:
            node = self._row_to_node(row)
            connection_string = row["connection_string"]
            result.append((node, connection_string))

        return result

    async def add_node_to_pool(self, pool_id: int, node_id: str) -> None:
        """Add a node to a pool.

        Args:
            pool_id: Pool ID
            node_id: Node ID
        """
        if not self._conn:
            await self.connect()

        await self._conn.execute(
            """
            INSERT INTO pool_memberships (pool_id, node_id, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT(pool_id, node_id) DO NOTHING
            """,
            (pool_id, node_id, datetime.now().isoformat()),
        )
        await self._conn.commit()

    async def get_pool_nodes(self, pool_id: int) -> list[Node]:
        """Get nodes in a pool.

        Args:
            pool_id: Pool ID

        Returns:
            List of Node objects
        """
        if not self._conn:
            await self.connect()

        query = """
            SELECT n.*
            FROM nodes n
            JOIN pool_memberships pm ON n.id = pm.node_id
            WHERE pm.pool_id = ?
            ORDER BY n.short_name
        """

        cursor = await self._conn.execute(query, [pool_id])
        rows = await cursor.fetchall()

        return [self._row_to_node(row) for row in rows]

    async def get_heard_nodes(self, seen_by: str | None = None) -> list[Node]:
        """Get nodes that are heard (not connected).

        Args:
            seen_by: Optional filter by which node heard them

        Returns:
            List of heard Node objects
        """
        if not self._conn:
            await self.connect()

        if seen_by:
            query = """
                SELECT DISTINCT n.*
                FROM nodes n
                LEFT JOIN connections c ON n.id = c.node_id
                JOIN heard_history h ON n.id = h.node_id
                WHERE c.node_id IS NULL AND h.seen_by = ?
                ORDER BY n.last_seen DESC
            """
            cursor = await self._conn.execute(query, (seen_by,))
        else:
            query = """
                SELECT n.*
                FROM nodes n
                LEFT JOIN connections c ON n.id = c.node_id
                WHERE c.node_id IS NULL
                ORDER BY n.last_seen DESC
            """
            cursor = await self._conn.execute(query)

        rows = await cursor.fetchall()
        return [self._row_to_node(row) for row in rows]

    async def get_connection(self, node_id: str) -> str | None:
        """Get connection string for a node.

        Args:
            node_id: Node ID

        Returns:
            Connection string or None if not connected
        """
        if not self._conn:
            await self.connect()

        cursor = await self._conn.execute(
            "SELECT connection_string FROM connections WHERE node_id = ?",
            (node_id,),
        )
        row = await cursor.fetchone()
        return row["connection_string"] if row else None

    def _row_to_node(self, row: aiosqlite.Row) -> Node:
        """Convert database row to Node object.

        Args:
            row: Database row

        Returns:
            Node object
        """
        return Node(
            id=row["id"],
            short_name=row["short_name"],
            long_name=row["long_name"],
            hw_model=row["hw_model"],
            firmware_version=row["firmware_version"],
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
            is_active=bool(row["is_active"]),
            snr=row["snr"],
            hops_away=row["hops_away"],
            config=json.loads(row["config"]),
        )

    def _row_to_pool(self, row: aiosqlite.Row) -> Pool:
        """Convert database row to Pool object.

        Args:
            row: Database row

        Returns:
            Pool object
        """
        return Pool(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            is_default=bool(row["is_default"]),
            created_at=datetime.fromisoformat(row["created_at"]),
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