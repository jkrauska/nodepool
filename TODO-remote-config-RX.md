# TODO: Complete Remote Config Retrieval

## Status: Partially Implemented

Remote firmware version retrieval is **working perfectly** ✅  
Full config retrieval is **partially implemented** and needs completion.

---

## What Works Now

✅ **Firmware Version Retrieval**
- Uses `interface.getNode(target_id).getMetadata()`
- Wraps callback to capture firmware from response packet
- Correctly returns remote node's firmware (e.g., 2.7.11.ee68575)
- Tested and working!

✅ **Basic Infrastructure**
- Callback wrapping mechanism works
- `capture_metadata_response()` proven functional
- `capture_config_response()` stub created

---

## What Needs to Be Done

### 1. Fix Compilation Errors

**File:** `src/nodepool/node_manager.py`

**Error:** Reference to undefined `metadata_response` variable
- Line references `metadata_response["firmware_version"]` 
- Should be `responses["firmware_version"]`
- Variable was renamed from `metadata_response` to `responses` dict

**Fix:**
```python
# OLD (wrong):
firmware_version = metadata_response["firmware_version"]

# NEW (correct):
firmware_version = responses["firmware_version"]
```

### 2. Implement `_build_config_from_responses()` Method

**Purpose:** Convert captured protobuf responses into config dict

**Location:** Add to `NodeManager` class in `src/nodepool/node_manager.py`

**Signature:**
```python
def _build_config_from_responses(self, responses: dict) -> dict[str, Any]:
    """Build config dict from captured protobuf responses.
    
    Args:
        responses: Dict with 'config' and 'module_config' keys containing protobufs
        
    Returns:
        Config dictionary suitable for Node.config field
    """
```

**Implementation:**
```python
def _build_config_from_responses(self, responses: dict) -> dict[str, Any]:
    """Build config dict from captured responses."""
    config = {}
    
    # Process LocalConfig sections
    for section_name, section_data in responses.get("config", {}).items():
        config[section_name] = {}
        # Iterate through protobuf fields
        for field in section_data.DESCRIPTOR.fields:
            field_value = getattr(section_data, field.name, None)
            if field_value is not None:
                config[section_name][field.name] = field_value
    
    # Process ModuleConfig sections
    for section_name, section_data in responses.get("module_config", {}).items():
        config[section_name] = {}
        for field in section_data.DESCRIPTOR.fields:
            field_value = getattr(section_data, field.name, None)
            if field_value is not None:
                config[section_name][field.name] = field_value
    
    return config
```

### 3. Fix Config Section Requests

**Current Implementation Issues:**

1. **Incomplete section list** - Only requests 7 LocalConfig and 3 ModuleConfig sections
2. **No error recovery** - If one section fails, should continue with others
3. **Slow sequential requests** - Takes ~30-60 seconds for full config

**LocalConfig Sections to Request:**
```python
config_sections = [
    ("device", config_pb2.Config.DESCRIPTOR.fields_by_name["device"]),
    ("position", config_pb2.Config.DESCRIPTOR.fields_by_name["position"]),
    ("power", config_pb2.Config.DESCRIPTOR.fields_by_name["power"]),
    ("network", config_pb2.Config.DESCRIPTOR.fields_by_name["network"]),
    ("display", config_pb2.Config.DESCRIPTOR.fields_by_name["display"]),
    ("lora", config_pb2.Config.DESCRIPTOR.fields_by_name["lora"]),
    ("bluetooth", config_pb2.Config.DESCRIPTOR.fields_by_name["bluetooth"]),
    # These are commented out in current implementation:
    # ("security", ...),  # May not be retrievable remotely
    # ("sessionkey", ...),  # May not be retrievable remotely
]
```

**ModuleConfig Sections to Request:**
```python
module_sections = [
    ("mqtt", module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name["mqtt"]),
    ("serial", module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name["serial"]),
    ("telemetry", module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name["telemetry"]),
    # Add these if needed:
    # ("external_notification", ...),
    # ("store_forward", ...),
    # ("range_test", ...),
    # ("canned_message", ...),
    # ("audio", ...),
    # ("remote_hardware", ...),
    # ("neighbor_info", ...),
    # ("ambient_lighting", ...),
    # ("detection_sensor", ...),
    # ("paxcounter", ...),
]
```

### 4. Update Node Creation

**Current:** Node is created with empty config
```python
node = Node(
    # ...
    config={},  # ← Empty!
)
```

**Needed:** Use built config
```python
full_config = self._build_config_from_responses(responses)

node = Node(
    id=target_node_id,
    short_name=user.get("shortName", "?"),
    long_name=user.get("longName", "Unknown"),
    hw_model=hw_model,
    firmware_version=firmware_version,
    last_seen=datetime.fromtimestamp(current_target_data.get("lastHeard", time.time())),
    is_active=True,
    snr=current_target_data.get("snr"),
    hops_away=current_target_data.get("hopsAway"),
    config=full_config,  # ← Use captured config!
)
```

### 5. Add CLI Flag for Full Config

**Current:** `nodepool remote config` only gets firmware

**Proposed:** Add `--full` flag
```bash
# Just metadata (current, fast ~5s)
nodepool remote config 29f35f73 --via 3c7f9d4e

# Full config (slow ~30-60s)
nodepool remote config 29f35f73 --via 3c7f9d4e --full
```

**Implementation in `cli.py`:**
```python
@click.option(
    "--full",
    is_flag=True,
    help="Request full configuration (slower, ~30-60s)"
)
def remote_config(via, dest, timeout, full):
    # ...
    if full:
        # Request all config sections
    else:
        # Just metadata (current behavior)
```

### 6. Improve Error Handling

**Issues to Handle:**
- Timeouts on individual config sections
- Target node doesn't support certain modules
- PKI auth failures
- Network errors mid-retrieval

**Pattern:**
```python
for section_name, section_field in config_sections:
    try:
        print(f"    - {section_name}", end="", flush=True)
        remote_node.requestConfig(section_field)
        interface.waitForAckNak()
        print(" ✓")
    except TimeoutError:
        print(" ⏱ timeout")
        logger.warning(f"Timeout getting {section_name}")
    except Exception as e:
        print(f" ✗ {e}")
        logger.error(f"Failed to get {section_name}: {e}")
        # Continue with next section
```

### 7. Add Progress Reporting

**Current:** Limited progress output

**Proposed:** Show percentage and time remaining
```
Requesting local config sections...
  - device ✓ (1/7)
  - position ✓ (2/7)
  - power ⏱ timeout (3/7)
  - network ✓ (4/7)
  ...
  
Retrieved 6/7 local config sections
Retrieved 2/3 module config sections
Total time: 42s
```

### 8. Test Coverage

**Need to test:**
1. Each config section individually
2. Timeout handling
3. Partial config retrieval (some sections fail)
4. Different target node types (router, client, tracker)
5. Nodes at different hop distances (1-hop, 3-hop, max-hop)
6. Error recovery and retry logic

---

## Testing Commands

```bash
# Test basic metadata (should work)
uv run nodepool remote config 29f35f73 --via 3c7f9d4e

# Test with longer timeout (for slow mesh)
uv run nodepool remote config 29f35f73 --via 3c7f9d4e --timeout 30

# Once --full is implemented
uv run nodepool remote config 29f35f73 --via 3c7f9d4e --full

# Verify saved config
uv run nodepool info 29f35f73
```

---

## Performance Considerations

**Remote Config Retrieval Time Estimates:**
- Metadata only: ~5-10 seconds (current)
- 7 LocalConfig sections: ~20-40 seconds
- 3 ModuleConfig sections: ~10-15 seconds
- **Total for full config: ~30-60 seconds**

**Optimization Ideas:**
1. Cache config sections (don't re-request if recent)
2. Allow selecting specific sections only
3. Parallel requests (if library supports)
4. Progressive updates (show partial results while waiting)

---

## Known Limitations

1. **Security sections** may not be retrievable remotely (intentional firmware restriction)
2. **Admin channel required** - Target must have admin channel enabled
3. **Mesh hops** - More hops = slower responses
4. **Firmware versions** - Older firmware may not support all config requests
5. **Node roles** - Some roles (relay, router) may have limited remote admin

---

## Files to Modify

1. **`src/nodepool/node_manager.py`**
   - Fix `metadata_response` → `responses` reference
   - Implement `_build_config_from_responses()`
   - Expand config section lists
   - Add progress reporting
   - Improve error handling

2. **`src/nodepool/cli.py`**
   - Add `--full` flag to `remote config` command
   - Update help text
   - Add progress display

3. **`tests/test_node_manager.py`**
   - Add tests for config capture
   - Test `_build_config_from_responses()`
   - Mock config responses

---

## Priority Order

1. **HIGH** - Fix compilation error (required for code to run)
2. **HIGH** - Implement `_build_config_from_responses()` (required for config data)
3. **MEDIUM** - Expand config section requests
4. **MEDIUM** - Add `--full` flag to CLI
5. **LOW** - Progress reporting improvements
6. **LOW** - Optimization and caching

---

## Reference: How Official CLI Does It

From `/tmp/meshtastic-python/meshtastic/node.py`:

```python
def requestConfig(self, configType):
    """Request the config from the node via admin message"""
    if self == self.iface.localNode:
        onResponse = None
    else:
        onResponse = self.onResponseRequestSettings
        print("Requesting current config from remote node (this can take a while).")
    
    p = admin_pb2.AdminMessage()
    if isinstance(configType, int):
        p.get_config_request = configType
    else:
        msgIndex = configType.index
        if configType.containing_type.name == "LocalConfig":
            p.get_config_request = msgIndex
        else:
            p.get_module_config_request = msgIndex
    
    self._sendAdmin(p, wantResponse=True, onResponse=onResponse)
    if onResponse:
        self.iface.waitForAckNak()
```

Key points:
- Uses `onResponseRequestSettings` callback
- Calls `waitForAckNak()` for remote nodes
- Distinguishes LocalConfig vs ModuleConfig by `containing_type.name`

---

## Current Working Example

**What works RIGHT NOW:**

```python
# Get remote node
remote_node = interface.getNode(target_node_id, requestChannelAttempts=0)

# Wrap callback
original_callback = remote_node.onRequestGetMetadata
def wrapped(packet):
    capture_metadata_response(packet)
    return original_callback(packet)
remote_node.onRequestGetMetadata = wrapped

# Request metadata
remote_node.getMetadata()

# Result: firmware_version captured in responses["firmware_version"]
```

This same pattern should work for `requestConfig()` with `onResponseRequestSettings`.

---

## Good Night! 🌙

The foundation is solid - firmware retrieval works perfectly. The full config implementation just needs the finishing touches above. Great progress tonight!