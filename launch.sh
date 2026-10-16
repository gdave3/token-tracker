#!/usr/bin/env bash
# Claude Token Tracker — launcher
# Starts the menu bar app + opens the dashboard in Safari

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Kill any existing instance
pkill -f "python3.*menubar.py" 2>/dev/null

# Start menu bar app in background
cd "$DIR"
nohup /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 "$DIR/menubar.py" > /tmp/token_tracker.log 2>&1 &
echo "Token Tracker started (PID $!)"

# Open dashboard in Safari
open -a Safari "$DIR/token-dashboard.html"
