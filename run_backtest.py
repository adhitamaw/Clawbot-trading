"""
Quick backtest using the engine + yfinance data
"""
import sys, json
sys.path.insert(0, '.')
import pandas as pd
import numpy as np
from datetime import datetime
from backtest.engine import BacktestEngine, BacktestConfig

# Load yfinance data
df = pd.read_csv('data/historical/XAUUSD_H1.csv', header=[0,1], skipinitialspace=True)
df.columns = ['date', 'close', 'high', 'low', 'open', 'volume']
df = df.iloc[2:].reset_index(drop=True)  # skip Ticker row

for c in ['open','high','low','close','volume']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df.dropna().reset_index(drop=True)
df['date'] = pd.to_datetime(df['date'])
df = df.set_index('date')

print(f"Data: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
print(f"Price: {df['close'].iloc[0]:.2f} -> {df['close'].iloc[-1]:.2f}")

# Simple signal: SMA crossover
df['sma_20'] = df['close'].rolling(20).mean()
df['sma_50'] = df['close'].rolling(50).mean()
df['signal'] = 0
df.loc[50:, 'signal'] = np.where(df.loc[50:, 'sma_20'] > df.loc[50:, 'sma_50'], 1.0, -1.0)

# Backtest
df['position'] = df['signal'].shift(1)
df['returns'] = df['close'].pct_change()
df['strategy_ret'] = df['position'] * df['returns']
df['equity'] = 10000 * (1 + df['strategy_ret'].fillna(0)).cumprod()

# Metrics
equity = df['equity']
total_ret = (equity.iloc[-1] / 10000 - 1) * 100
sharpe = np.sqrt(365*24) * df['strategy_ret'].mean() / df['strategy_ret'].std()
cummax = equity.cummax()
dd = ((cummax - equity) / cummax * 100)
max_dd = dd.max()
win = df[df['strategy_ret'] > 0]
loss = df[df['strategy_ret'] < 0]
wr = len(win) / (len(win) + len(loss)) * 100 if (len(win) + len(loss)) > 0 else 0
pf = win['strategy_ret'].sum() / abs(loss['strategy_ret'].sum()) if abs(loss['strategy_ret'].sum()) > 0 else 0
trades = (df['signal'].diff() != 0).sum()
recovery = total_ret / max_dd if max_dd > 0 else 0

print(f"""
📊 BACKTEST — SMA CROSSOVER (20/50) ON XAUUSD H1
{'='*50}
  Period:    {df.index[0].strftime('%Y-%m-%d')} -> {df.index[-1].strftime('%Y-%m-%d')}
  Bars:      {len(df):,}
  
  Start:     $10,000
  End:       ${equity.iloc[-1]:,.2f}
  Return:    {total_ret:+.2f}%
  
  Sharpe:    {sharpe:.2f}
  Max DD:    {max_dd:.2f}%
  Win Rate:  {wr:.1f}%
  Profit F:  {pf:.2f}
  Recovery:  {recovery:.2f}
  Trades:    {trades}
  
  {'✅ PASS' if sharpe > 1.0 and max_dd < 15 and pf > 1.5 else '❌ NEEDS IMPROVEMENT'}
""")
