# Remote Config Retrieval Status

## Current Status: FIRMWARE ONLY ✅

Remote firmware version retrieval is **working perfectly** ✅  
Full config retrieval is **blocked by library limitations** ⚠️

### What Works
- ✅ Firmware version retrieval via `getMetadata()`
- ✅ Hardware model detection
- ✅ Basic node information (SNR, hops, last heard)

### What Doesn't Work
- ⚠️ Full config section retrieval over mesh
- ⚠️ Multi-section config requests return cached data (0.1s response times)

## Root Cause: Library Config Caching

The Meshtastic Python library caches the entire config tree after the first successful `getMetadata()` or config section retrieval. This causes:

1. **First request** (e.g., `device` config): Real network request → 14.9s
2. **Subsequent requests**: Library returns cached data → 0.1s (no network traffic)

### Evidence
```
[1/10] Received device config ✓ (14.9s)  ← Real network response
[2/10] Received position config ✓ (0.1s)  ← Cached!
[3/10] Received power config ✓ (0.1s)     ← Cached!
[4/10] Received network config ✓ (0.1s)   ← Cached!
...
```

### Why This Happens

After the first config retrieval, the library populates:
- `remote_node.localConfig` (all sections)
- `remote_node.moduleConfig` (all sections)

When `waitForConfig()` is called again, it sees these objects exist and returns `True` immediately without waiting for new network data.

## Potential Solutions

### Option 1: Bypass Library Config System
Manually construct admin requests and parse responses without using `remote_node.localConfig/moduleConfig`:

```python
# Send admin request
p = admin_pb2.AdminMessage()
p.get_config_request = section_field.index
remote_node._sendAdmin(p, wantResponse=True)

# Wait for actual admin response packet (not waitForConfig)
# Parse response protobuf directly
# Don't read from remote_node.localConfig
```

**Pros:** Would get real network responses  
**Cons:** Bypasses library's built-in parsing, increases complexity

### Option 2: Clear Cache Between Requests
Try to clear or reset `remote_node.localConfig` between requests:

```python
# Before each request
remote_node.localConfig = None  # or reset to empty
# Then request
remote_node.requestConfig(section_field)
```

**Pros:** Simple if it works  
**Cons:** May not be supported, could break library internals

### Option 3: Use Stream Interceptor
Already have `MessageResponseHandler` that intercepts packets at stream level:

```python
# Register packet ID to track
handler.register_packet(packet_id)

# Send config request
remote_node.requestConfig(section_field)

# Wait for admin response packet directly
admin_response = handler.wait_for_admin_response(packet_id, timeout=20)

# Parse the admin message for config data
if admin_response:
    admin_msg = admin_response["admin_message"]
    # Extract config from admin_msg.get_config_response
```

**Pros:** Bypasses library caching, gets real network responses  
**Cons:** Requires manual protobuf parsing

### Option 4: Request All Sections at Once
Some firmwares support requesting all config at once:

```python
# Single request for all configs
remote_node.requestChannels()  # If supported
```

**Pros:** Single network roundtrip  
**Cons:** May not be supported by all firmware versions

## Recommended Solution: Option 3

Use the existing `MessageResponseHandler` stream interceptor to capture admin responses directly:

```python
# Install interceptor
handler = MessageResponseHandler(interface)

for section_name, section_field in config_sections:
    # Send request
    p = admin_pb2.AdminMessage()
    p.get_config_request = section_field.index
    packet_id = remote_node._sendAdmin(p, wantResponse=True)
    
    # Register for tracking
    handler.register_packet(packet_id)
    
    # Wait for real admin response (not waitForConfig!)
    admin_response = handler.wait_for_admin_response(packet_id, timeout=20)
    
    if admin_response:
        # Parse config from admin_response["admin_message"]
        admin_msg = admin_response["admin_message"]
        if hasattr(admin_msg, "get_config_response"):
            config_data = admin_msg.get_config_response
            # Store in responses dict
            responses["config"][section_name] = config_data
```

This would:
- ✅ Get real network responses (15-20s each)
- ✅ Not rely on library caching
- ✅ Use existing stream interceptor infrastructure
- ✅ Parse protobufs directly

## Current Workaround

For now, `nodepool remote config` only retrieves firmware version, which works reliably. The code documents this limitation with:

```python
# Note: Full config retrieval over mesh is not currently reliable
# The library caches config after first retrieval, making subsequent
# requests appear successful (0.1s) but actually returning cached data
# rather than fresh network responses.
```

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