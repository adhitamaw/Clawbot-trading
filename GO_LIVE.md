# GO-LIVE Checklist — XAU/USD Trading System v1.0

## Pre-Deployment (VPS)

- [ ] Ubuntu 24.04 LTS installed and updated (`apt update && apt upgrade -y`)
- [ ] Python 3.11+ installed (`python3 --version`)
- [ ] TA-Lib installed (`apt install ta-lib`)
- [ ] Create non-root user `xau_trader`
- [ ] Clone repo to `/opt/xauusd_trading_system_v1`
- [ ] `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
- [ ] Copy `config/.env.example` → `config/.env` and fill ALL values
- [ ] MetaTrader 5 terminal installed and logged into broker account
- [ ] XAUUSD and DXY symbols visible in MT5 Market Watch
- [ ] MT5 AutoTrading enabled (Ctrl+E or button in toolbar)

## Configuration

- [ ] `.env` file has valid credentials (never committed!)
- [ ] `config.yaml` parameters reviewed for your broker's specifics
- [ ] MT5 server name confirmed (check MT5 terminal → Navigator → Accounts)
- [ ] Telegram bot token and chat ID set (create bot via @BotFather)
- [ ] News API key set (Trading Economics or Finnhub)
- [ ] Database credentials set (or use SQLite fallback)

## Security Hardening

- [ ] `.env` permissions: `chmod 600 config/.env`
- [ ] No exposed ports except SSH
- [ ] UFW firewall: `ufw allow ssh && ufw enable`
- [ ] Fail2ban installed: `apt install fail2ban`
- [ ] SSH key-only auth (disable password login)
- [ ] Dedicated non-root user for trading system
- [ ] GitHub repo is private (protect strategy IP)

## Docker Deployment (Alternative)

- [ ] `docker compose -f docker/docker-compose.yml up -d`
- [ ] Verify container running: `docker ps | grep xau_trading`
- [ ] Verify logs: `docker logs -f xau_trading_v1`
- [ ] Ensure `.env` file is in project root for Docker

## Systemd Service

- [ ] Copy `deploy/xau-trading.service` → `/etc/systemd/system/`
- [ ] Edit service file to match your paths
- [ ] `systemctl daemon-reload`
- [ ] `systemctl enable xau-trading`
- [ ] `systemctl start xau-trading`
- [ ] `systemctl status xau-trading`

## Watchdog

- [ ] Copy `deploy/watchdog.sh` → `/usr/local/bin/xau-watchdog`
- [ ] `chmod +x /usr/local/bin/xau-watchdog`
- [ ] Add cron: `*/1 * * * * /usr/local/bin/xau-watchdog`
- [ ] Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars for cron

## Testing Pipeline (MANDATORY)

### 1. Smoke Test
- [ ] Start system and verify logs: `journalctl -u xau-trading -f`
- [ ] Confirm MT5 connection successful (check logs)
- [ ] Confirm Telegram startup message received
- [ ] Verify heartbeat file exists: `ls -la /tmp/xau_heartbeat`

### 2. Demo Paper Trading (1-3 months minimum)
- [ ] Run on MT5 demo account for minimum 1 month
- [ ] Verify trades are opened/closed correctly
- [ ] Check all filters working (news, anomaly, intermarket)
- [ ] Monitor Telegram alerts for false positives
- [ ] Record daily P&L and compare to system logs
- [ ] Verify no unexplained system crashes/restarts

### 3. Minimum-Lot Live Incubation (4-8 weeks minimum)
- [ ] Switch to live account with 0.01 lot minimum
- [ ] Set config.mp5.lot_max = 0.01 temporarily
- [ ] Verify execution latency < 250ms
- [ ] Track actual slippage vs estimated
- [ ] Monitor swap charges match expectations
- [ ] Compare live results to demo paper trading

### 4. Performance Gates (All must pass)
- [ ] Win Rate ≥ 58%
- [ ] Profit Factor ≥ 1.8
- [ ] Sharpe Ratio ≥ 1.5
- [ ] Maximum Drawdown ≤ 8%
- [ ] Recovery Factor ≥ 3.0

## Gradual Scaling (only after ALL gates passed)
- [ ] Increase risk from 0.01 to 0.02 lots
- [ ] Monitor for 2+ weeks at each level
- [ ] Never exceed max_lot from risk calculation
- [ ] Never override circuit breaker settings

## Monitoring (Daily)

- [ ] Check Telegram for overnight alerts
- [ ] Review daily summary at 23:55 UTC
- [ ] Check journalctl logs for warnings
- [ ] Verify MT5 terminal still running
- [ ] Verify heartbeat is fresh
- [ ] Check equity vs daily start

## Weekly Maintenance

- [ ] Review model retraining logs
- [ ] Check anomaly detection false positive rate
- [ ] Update economic calendar (or verify auto-sync)
- [ ] Review weekly P&L breakdown by regime
- [ ] Git pull latest code changes

## Emergency Procedures

### If hard circuit breaker triggers:
1. DO NOT restart immediately
2. Review logs to understand cause
3. Check if news event caused unusual volatility
4. Verify anomaly detection was correct
5. Wait for daily reset or manual confirmation

### If system crashes:
1. Check `journalctl -u xau-trading -n 100`
2. Check disk space: `df -h`
3. Check RAM: `free -h`
4. Restart: `systemctl restart xau-trading`
5. Verify MT5 terminal is still running

### If anomaly detection false-positives frequently:
1. Check anomaly config thresholds
2. Review recent market conditions
3. Consider adjusting zscore_threshold or cooldown_minutes
4. Do NOT disable anomaly detection entirely

## Support

- Repo: https://github.com/adhitamaw/Clawbot-trading
- Logs: `/opt/xauusd_trading_system_v1/logs/`
- Audit trail: `/opt/xauusd_trading_system_v1/logs/audit/`
- Config: `/opt/xauusd_trading_system_v1/config/config.yaml`

---

**DO NOT SKIP THE TESTING PIPELINE.**

Paper trading → minimum-lot live → gradual scaling is MANDATORY.
Never deploy with full position sizing without passing all gates.
