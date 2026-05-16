#!/bin/bash
export WINEPREFIX=/home/headless/.wine
export DISPLAY=:1
export MT5_WIN="C:\Program Files\MetaTrader 5"
export MT5_NIX="/home/headless/.wine/drive_c/Program Files/MetaTrader 5"

# Copy mt5.ini from MQL5/Files if available
if [ -f "$MT5_NIX/MQL5/Files/mt5.ini" ] && [ ! -f "$MT5_NIX/mt5.ini" ]; then
    cp "$MT5_NIX/MQL5/Files/mt5.ini" "$MT5_NIX/mt5.ini"
fi

# Start MT5 terminal with proper Windows paths
wine "$MT5_NIX/terminal64.exe" /config:"$MT5_WIN\mt5.ini" &
PID=$!

# Wait for terminal to initialize
sleep 25

# Monitor terminal
while kill -0 $PID 2> /dev/null; do
    sleep 10
done

echo "MetaTrader exited, stopping container"
exit 1
