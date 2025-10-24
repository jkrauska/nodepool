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

Set PlaybackSpeed 1.5

# Then set prompt inside the shell
Hide
Type "PS1='\033[33m$\033[0m '"
Enter
Ctrl+L
Show

# Title
Type "# Nodepool - Meshtastic Node Pool Management"
Enter
Sleep 1s

# Discover nodes
Type "nodepool discover"
Sleep 500ms
Enter
Wait+Line@30s /.*Total.*/
Sleep 2s

Type "# List all connected nodes"
Enter
Type "nodepool list"
Sleep 500ms
Enter
Sleep 3s

Type "# Sync heard nodes from connected devices"
Enter
Type "nodepool sync"
Sleep 500ms
Enter
Wait+Line@30s /.*Successfully synced.*/
Sleep 2s

# # Show node info
# Type "clear"
# Enter
# Sleep 500ms

# Type "# Get detailed info about a node"
# Enter
# Type "nodepool info !ccddeeff"
# Sleep 500ms
# Enter
# Sleep 3s

# # Sync heard nodes
# Type "clear"
# Enter
# Sleep 500ms



# # Show heard nodes
# Type "clear"
# Enter
# Sleep 500ms

# Type "# Show nodes heard on the mesh"
# Enter
# Type "nodepool heard"
# Sleep 500ms
# Enter
# Sleep 3s


# Final pause
Sleep 2s