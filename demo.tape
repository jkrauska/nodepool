# VHS Demo Script for Nodepool CLI
# Generate with: vhs demo.tape
# Requires: brew install vhs

Output demo.gif

Set FontSize 14
Set Width 1400
Set Height 800
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 50ms

# Title
Type "# Nodepool - Meshtastic Node Pool Management"
Enter
Sleep 1s

# Discover nodes
Type "nodepool discover --network"
Sleep 500ms
Enter
Sleep 4s

# Clear and show list
Type "clear"
Enter
Sleep 500ms

Type "# List all connected nodes"
Enter
Type "nodepool list"
Sleep 500ms
Enter
Sleep 3s

# Show node info
Type "clear"
Enter
Sleep 500ms

Type "# Get detailed info about a node"
Enter
Type "nodepool info !ccddeeff"
Sleep 500ms
Enter
Sleep 3s

# Sync heard nodes
Type "clear"
Enter
Sleep 500ms

Type "# Sync heard nodes from connected devices"
Enter
Type "nodepool sync"
Sleep 500ms
Enter
Sleep 4s

# Show heard nodes
Type "clear"
Enter
Sleep 500ms

Type "# Show nodes heard on the mesh"
Enter
Type "nodepool heard"
Sleep 500ms
Enter
Sleep 3s

# Check configuration
Type "clear"
Enter
Sleep 500ms

Type "# Check node configurations"
Enter
Type "nodepool check"
Sleep 500ms
Enter
Sleep 3s

# Final pause
Sleep 2s