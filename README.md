# nodepool

Manage and maintain a group of Meshtastic nodes with a powerful CLI and database-backed tracking.

## Features

- 🔍 **Auto-discovery**: Scan serial ports and network via mDNS to find Meshtastic nodes
- 📊 **Node tracking**: SQLite database to track node inventory and configurations
- ✅ **Configuration validation**: Verify TTL, region, and channel settings across all nodes
- 🔌 **Reachability checks**: Test if nodes are online and responding
- 📁 **Export**: Save node configurations to JSON/YAML
- ⚡ **Async operations**: Concurrent node operations for speed
- 🎨 **Rich output**: Beautiful tables and formatted console output
- 🌐 **Network discovery**: Find TCP-enabled nodes via mDNS
- 📡 **Heard nodes**: Track nodes heard on the mesh network

## Demo

See nodepool in action:

![Demo](demo.gif)

*To generate the demo yourself: `vhs demo.tape` (requires [VHS](https://github.com/charmbracelet/vhs))*

## Requirements

- Python 3.13+
- Serial-connected Meshtastic devices
- `uv` package manager (recommended)

## Installation

### Using uv (recommended)

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/yourusername/nodepool.git
cd nodepool

# Create virtual environment and install
uv venv --python 3.13
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

### Using pip

```bash
pip install -e .
```

## Quick Start

1. **Discover nodes** on your serial ports:
   ```bash
   nodepool discover
   ```

2. **List all nodes** in your pool:
   ```bash
   nodepool list
   ```

3. **Check configurations** across all nodes:
   ```bash
   nodepool check --ttl 7 --region US
   ```

4. **Check node status**:
   ```bash
   nodepool status
   ```

## Commands

### `nodepool discover`

Scan serial ports and add discovered nodes to the database.

```bash
nodepool discover [OPTIONS]

Options:
  --db PATH          Database file path (default: nodepool.db)
  --ports TEXT       Specific serial ports to scan (multiple)
```

Examples:
```bash
# Auto-discover on all ports
nodepool discover

# Scan specific ports
nodepool discover --ports /dev/ttyUSB0 --ports /dev/ttyUSB1
```

### `nodepool list`

Display all nodes in the pool.

```bash
nodepool list [OPTIONS]

Options:
  --db PATH    Database file path
  --all        Show inactive nodes as well
```

### `nodepool info`

Show detailed information about a specific node.

```bash
nodepool info NODE_ID [OPTIONS]

Options:
  --db PATH    Database file path
```

Example:
```bash
nodepool info !abc123
```

### `nodepool check`

Run configuration validation checks on all nodes.

```bash
nodepool check [OPTIONS]

Options:
  --db PATH         Database file path
  --ttl INTEGER     Expected TTL/hop limit (default: 7)
  --region TEXT     Expected LoRa region (e.g., US, EU_868)
```

Examples:
```bash
# Check TTL only
nodepool check --ttl 7

# Check TTL and region
nodepool check --ttl 7 --region US
```

### `nodepool status`

Check reachability of all nodes.

```bash
nodepool status [OPTIONS]

Options:
  --db PATH    Database file path
```

### `nodepool sync`

Sync heard nodes from connected managed node(s).

```bash
nodepool sync [OPTIONS]

Options:
  --db PATH     Database file path
  --port TEXT   Specific serial port to sync from
```

Examples:
```bash
# Sync from all managed nodes
nodepool sync

# Sync from specific port
nodepool sync --port /dev/ttyUSB0
```

### `nodepool sync_meshview`

Sync nodes from the MeshView API (alternative to direct node connection).

```bash
nodepool sync_meshview [OPTIONS]

Options:
  --db PATH           Database file path
  --days-active INT   Number of days of activity to filter by (default: 3)
  --url TEXT          MeshView API base URL (default: https://meshview.bayme.sh)
```

Examples:
```bash
# Sync nodes active in last 3 days
nodepool sync_meshview

# Sync nodes active in last 7 days
nodepool sync_meshview --days-active 7

# Use custom MeshView instance
nodepool sync_meshview --url https://custom.meshview.example.com
```

**Note**: Nodes synced from MeshView are marked as heard from `meshviewAPI` and are not managed nodes (no direct serial connection).

### `nodepool heard`

List nodes heard on the mesh network (not directly connected).

```bash
nodepool heard [OPTIONS]

Options:
  --db PATH        Database file path
  --seen-by TEXT   Filter by managed node that heard them
```

Examples:
```bash
# List all heard nodes
nodepool heard

# List nodes heard by specific managed node
nodepool heard --seen-by !abc123
```

### `nodepool export`

Export node configurations to JSON or YAML.

```bash
nodepool export [OPTIONS]

Options:
  --db PATH             Database file path
  --output, -o PATH     Output file (default: stdout)
  --format [json|yaml]  Output format (default: json)
```

Examples:
```bash
# Export to stdout
nodepool export

# Export to file
nodepool export -o nodes.json

# Export as YAML
nodepool export -o nodes.yaml --format yaml
```

## Configuration Checks

The tool validates:

- **TTL/Hop Limit**: Ensures consistent hop limits across nodes
- **Region**: Verifies all nodes use the same LoRa region
- **Channels**: Checks for presence of secondary channels (if configured)

Check results are stored in the database for historical tracking.

## Database Schema

### nodes
Stores node inventory and current configuration:
- Node ID, names, hardware info
- Serial port connection
- Firmware version
- Last seen timestamp
- Active status
- Current configuration (JSON)

### config_snapshots
Historical configuration backups:
- Node ID
- Timestamp
- Full configuration (JSON)

### config_checks
Validation results:
- Node ID
- Check type (ttl, region, channel)
- Expected vs actual values
- Status (pass/fail/warning)
- Timestamp

## Development

### Setup Development Environment

```bash
# Install with dev dependencies
uv pip install -e ".[dev]"

# Install pre-commit hooks (optional)
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=nodepool --cov-report=html

# Run specific test file
pytest tests/test_database.py

# Verbose output
pytest -v
```

### Code Quality

```bash
# Lint code
ruff check .

# Format code
ruff format .

# Type check (if mypy is installed)
mypy src/
```

### Project Structure

```
nodepool/
├── src/nodepool/           # Main package
│   ├── __init__.py
│   ├── models.py           # Pydantic data models
│   ├── database.py         # Async SQLite operations
│   ├── node_manager.py     # Meshtastic node operations
│   ├── config_checker.py   # Configuration validation
│   └── cli.py              # Click CLI commands
├── tests/                  # Test suite
│   ├── conftest.py         # Shared test fixtures
│   ├── test_database.py
│   ├── test_node_manager.py
│   ├── test_config_checker.py
│   └── test_cli.py
├── pyproject.toml          # Project configuration
├── .clinerules             # Cline AI context
└── README.md
```

## Architecture

### Async Design

The tool uses Python's `asyncio` for concurrent operations:

- **Concurrent scanning**: Scan multiple serial ports simultaneously
- **Batch checks**: Check all nodes in parallel
- **Async database**: Non-blocking SQLite operations with aiosqlite

### Meshtastic Integration

- Uses the official `meshtastic` Python library
- Serial operations run in thread pool to avoid blocking
- Handles connection failures gracefully
- Extracts node info, firmware, and configuration

### Database

- SQLite for local storage
- Async operations via aiosqlite
- Upsert pattern for node updates
- Context managers for safe connection handling

## Roadmap

### Phase 1 (Current) - Read-only Operations
- ✅ Node discovery
- ✅ Configuration tracking
- ✅ Validation checks
- ✅ Reachability testing
- ✅ Export functionality

### Phase 2 - Write Operations
- [ ] Sync configurations across nodes
- [ ] Update TTL on all nodes
- [ ] Add/modify channels
- [ ] Bulk firmware updates

### Phase 3 - REST API
- [ ] FastAPI backend
- [ ] WebSocket for real-time updates
- [ ] Authentication

### Phase 4 - Web Dashboard
- [ ] React/Vue frontend
- [ ] Real-time monitoring
- [ ] Configuration management UI

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Write tests for new features
4. Ensure all tests pass
5. Submit a pull request

## License

Apache License 2.0 - see LICENSE file for details.

## Troubleshooting

### No nodes discovered

- Check USB connections
- Verify serial port permissions: `sudo usermod -a -G dialout $USER`
- Try specifying ports manually: `--ports /dev/ttyUSB0`

### Database locked errors

- Ensure no other nodepool process is running
- Check file permissions on nodepool.db

### Import errors

- Activate virtual environment: `source .venv/bin/activate`
- Reinstall: `uv pip install -e ".[dev]"`

## Support

- GitHub Issues: https://github.com/yourusername/nodepool/issues
- Meshtastic Discord: https://discord.gg/meshtastic