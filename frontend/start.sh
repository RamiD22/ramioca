#!/bin/bash
export PATH="/opt/homebrew/bin:$PATH"
cd "$(dirname "$0")"
exec npx vite --port 5173 --strictPort --host 127.0.0.1
