#!/bin/bash
# Smart Playlist Creator by Deckard — launcher
cd "$(dirname "$0")"

# Install deps if needed
if ! python3 -c "import flask" 2>/dev/null; then
  echo "Installing Flask..."
  pip3 install flask --break-system-packages -q
fi

echo ""
echo "  ██████╗ ███████╗ ██████╗██╗  ██╗ █████╗ ██████╗ ██████╗"
echo "  ██╔══██╗██╔════╝██╔════╝██║ ██╔╝██╔══██╗██╔══██╗██╔══██╗"
echo "  ██║  ██║█████╗  ██║     █████╔╝ ███████║██████╔╝██║  ██║"
echo "  ██║  ██║██╔══╝  ██║     ██╔═██╗ ██╔══██║██╔══██╗██║  ██║"
echo "  ██████╔╝███████╗╚██████╗██║  ██╗██║  ██║██║  ██║██████╔╝"
echo "  ╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝"
echo ""
echo "  Smart Playlist Creator by Deckard"
echo "  http://localhost:${SPC_PORT:-5001}"
echo ""

python3 app.py
