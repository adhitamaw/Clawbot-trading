#!/bin/bash
# Wait for MT5 to be ready, then setup Python + RPC server

echo "Waiting for MT5 container to be ready..."
while true; do
  if docker exec xau_mt5 ps aux 2>/dev/null | grep -q terminal64; then
    echo "MT5 is running!"
    break
  fi
  sleep 5
done

# Wait for MT5 to finish updating
sleep 20

echo "=== Step 1: Download Python for Windows ==="
docker exec xau_mt5 wget -q -O /home/headless/python_installer.exe \
  "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe" 2>&1

echo "=== Step 2: Install Python in Wine ==="
docker exec xau_mt5 env DISPLAY=:1 wine /home/headless/python_installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 2>&1
sleep 5

echo "=== Step 3: Install pymt5linux (includes MetaTrader5) ==="
# Install an older version that works with Wine
docker exec xau_mt5 env DISPLAY=:1 wine "C:/Program Files/Python312/python.exe" -m pip install pymt5linux 2>&1

echo "=== Step 4: Start RPC Server ==="
# Start the pymt5linux RPC server
docker exec -d xau_mt5 env DISPLAY=:1 wine "C:/Program Files/Python312/python.exe" -m pymt5linux --host 0.0.0.0 --port 8001 "C:/Program Files/Python312/python.exe"

echo "=== Setup complete! ==="
echo "MT5 RPC server should be running on port 8001"
