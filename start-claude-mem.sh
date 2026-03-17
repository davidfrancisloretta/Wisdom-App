#!/bin/bash
# Start Claude-Mem worker in background
export PATH="$PATH:$HOME/.bun/bin"
bun "$HOME/.claude/plugins/marketplaces/thedotmack/scripts/worker-service.cjs" run &
echo "Claude-Mem worker started (PID: $!)"
echo "Viewer UI: http://localhost:37777"
