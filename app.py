import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta
import os
import math
from streamlit_autorefresh import st_autorefresh

# --- 0. 基礎設定 ---
DATA_FILE = 'trades.csv'
st.set_page_config(page_title="NVDA 戰情中心 V6", layout="wide")

# 每 2 秒自動刷新數據 (驅動報價跳動)
st_autorefresh(interval=2000, limit=None, key="nvda_auto_refresh")

# --- 1. 預留固定容器 (防止畫面跳動的關鍵) ---
# 先定義好位置，後續使用 .container() 填充內容
header_placeholder = st.empty()
metric_placeholder = st.empty()
strategy_placeholder = st.empty()
chart_placeholder = st.empty()

# --- 2. 資料存取與計算函數 ---
@st.cache_data(ttl=3600) # 圖表與歷史指標快取 1 小時，避免重繪閃爍
def get_hist_analysis(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    df = stock.history(period="2y", auto_adjust=False)
    if df.empty: return None
    # 原代碼指標邏輯
    df['SMA20'] = df['Close'].rolling(20).mean()
    df['SMA60'] = df['Close'].rolling(60).mean()
    df['SMA200'] = df['Close'].rolling(200).mean()
    std = df['Close'].rolling(20).std()
    df['BB_upper'] = df['SMA20'] + 2 * std
    df['BB_lower'] = df['SMA20'] - 2 * std
    df['BB_pos'] = (df['Close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower'] + 1e-9) * 100
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    df['RSI'] = 100 - (100 / (1 + (gain/(loss + 1e-9))))
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']
    return df

def get_realtime_quote(ticker_symbol):
    try:
        t = yf.Ticker(ticker_symbol)
        info = t.fast_info
        p, pc = info.last_price, info.previous_close
        if p is None or pc is None: return None, 0, 0
        return p, (p - pc), ((p - pc) / pc * 100)
    except: return None, 0, 0

def load_trades():
    if os.path.exists(DATA_FILE):
        return pd.read_csv(DATA_FILE)
    return pd.DataFrame(columns=['Date', 'Type', 'Price', 'Shares', 'Total'])

# --- 3. 執行邏輯 ---
hist = get_hist_analysis("NVDA")
curr_p, curr_c, curr_pct = get_realtime_quote("NVDA")
# 台灣時間同步
tw_time = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')

# --- 4. 填充容器內容 ---

# A. 頂部報價區
with header_placeholder.container():
    st.title("🚀 NVDA 戰情室 V6 (Live)")
    if curr_p is not None:
        color = "#ff4b4b" if curr_c < 0 else "#00c805"
        st.markdown(f"""
            <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; border-left:8px solid {color};">
                <h2 style="margin:0; color:white;">即時報價: <span style="color:{color};">${curr_p:.2f} ({'+' if curr_c>=0 else ''}{curr_c:.2f} / {'+' if curr_c>=0 else ''}{curr_pct:.2f}%)</span></h2>
                <small style="color:gray;">最後同步 (台北): {tw_time}</small>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.info("正在連線至交易所獲取最新數據...")

# B. 資產概況 (維持原計算邏輯)
initial_capital = 31925
trades = load_trades()
total_shares, cash, invested_cost = 0, initial_capital, 0
for _, r in trades.iterrows():
    amt = r['Price'] * r['Shares']
    if "買入" in r['Type']:
        total_shares += r['Shares']; cash -= amt; invested_cost += amt
    else:
        total_shares -= r['Shares']; cash += amt
        if total_shares > 0: invested_cost *= (1 - (r['Shares'] / (total_shares + r['Shares'])))

display_p = curr_p if curr_p else hist['Close'].iloc[-1]
mkt_val = total_shares * display_p
pl_val = (cash + mkt_val) - initial_capital

with metric_placeholder.container():
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("持倉市值", f"${mkt_val:.2f}", f"{total_shares:.0f} 股")
    c2.metric("手中現金", f"${cash:.2f}")
    c3.metric("總損益", f"${pl_val:.2f}", f"{(pl_val/initial_capital*100):.2f}%")

# C. 策略核心 (維持原 V6 邏輯)
with strategy_placeholder.container():
    if hist is not None:
        row = hist.iloc[-1]
        sma200, sma60, sma20 = row['SMA200'], row['SMA60'], row['SMA20']
        rsi, bb_pos, hist_val = row['RSI'], row['BB_pos'], row['Hist']
        bull_trend = display_p > sma200
        score = sum([rsi < (40 if bull_trend else 30), bb_pos < 15, hist_val > hist['Hist'].iloc[-2], bull_trend])
        
        # 決策邏輯
        action = "HOLD"; shares = 0
        if total_shares > 0 and not (bull_trend and display_p > sma60) and (rsi > (78 if bull_trend else 70) or bb_pos > 85 or (display_p < sma60 and sma20 < sma60)):
            action = "SELL"; shares = math.ceil(total_shares * 0.25)
        elif cash > 0 and (mkt_val / initial_capital) < 0.6:
            if score >= 3: action = "STRONG_BUY"; shares = math.floor((cash * 0.4 * max(0.2, 1-abs(display_p-sma200)/sma200)) / display_p)
        
        st.subheader("🧠 策略訊號")
        st.metric("建議行動", action, f"{shares} 股")

# D. 技術圖表 (因為快取保護，不隨 2 秒刷新重新繪製)
with chart_placeholder.container():
    st.divider()
    st.subheader("📈 技術分析 (靜態歷史圖表)")
    if hist is not None:
        chart_df = hist.tail(126)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        fig.add_trace(go.Candlestick(x=chart_df.index, open=chart_df['Open'], high=chart_df['High'], low=chart_df['Low'], close=chart_df['Close'], name='K線'), row=1, col=1)
        for ma, clr in zip(['SMA20','SMA60','SMA200'], ['orange','cyan','purple']):
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df[ma], line=dict(color=clr, width=1.5), name=ma), row=1, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

# 側邊欄紀錄顯示
with st.sidebar:
    st.subheader("📋 歷史交易紀錄")
    st.dataframe(trades.sort_index(ascending=False), use_container_width=True)
