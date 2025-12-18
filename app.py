import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta
import os
import math
import gspread
import time
from streamlit_autorefresh import st_autorefresh

# --- 0. 基礎設定 ---
# 這裡維持您原本使用的 Google Sheet 名稱
PORTFOLIO_SHEET_TITLE = 'Streamlit NVDA' 
st.set_page_config(page_title="NVDA 戰情中心 V6", layout="wide")

# 每 2 秒觸發刷新 (僅針對數據)
st_autorefresh(interval=2000, limit=None, key="nvda_auto_refresh")

st.title("🚀 NVDA 戰情室 V6 (Smooth Update)")

# --- 1. 資料存取函數 ---
def get_gsheet_client():
    credentials = st.secrets["gcp_service_account"]
    return gspread.service_account_from_dict(credentials)

def load_trades():
    try:
        gc = get_gsheet_client()
        sh = gc.open(PORTFOLIO_SHEET_TITLE).sheet1
        data = sh.get_all_records()
        if not data:
            return pd.DataFrame(columns=['Date', 'Type', 'Price', 'Shares', 'Total'])
        df = pd.DataFrame(data)
        # 確保格式正確
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
        df['Shares'] = pd.to_numeric(df['Shares'], errors='coerce')
        df['Total'] = pd.to_numeric(df['Total'], errors='coerce')
        return df
    except Exception:
        return pd.DataFrame(columns=['Date', 'Type', 'Price', 'Shares', 'Total'])

def save_trade(date_val, trans_type, price, shares):
    try:
        gc = get_gsheet_client()
        sh = gc.open(PORTFOLIO_SHEET_TITLE).sheet1
        total_amt = price * shares
        new_row = [str(date_val), trans_type, float(price), float(shares), float(total_amt)]
        sh.append_row(new_row)
        return True
    except Exception as e:
        st.error(f"儲存失敗: {e}")
        return False

# --- 2. 數據獲取與快取邏輯 (關鍵：減少圖表重新計算) ---

@st.cache_data(ttl=3600) # 圖表運算快取 1 小時，避免每 2 秒閃爍
def get_nvda_analysis(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    df = stock.history(period="2y", auto_adjust=False)
    if df.empty: return None
    
    # 均線計算
    df['SMA20'] = df['Close'].rolling(20).mean()
    df['SMA60'] = df['Close'].rolling(60).mean()
    df['SMA200'] = df['Close'].rolling(200).mean()
    
    # 布林
    std = df['Close'].rolling(20).std()
    df['BB_upper'] = df['SMA20'] + 2 * std
    df['BB_lower'] = df['SMA20'] - 2 * std
    df['BB_pos'] = (df['Close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower'] + 1e-9) * 100
    
    # RSI
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']
    return df

def get_realtime_data(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.fast_info
        curr_p = info.last_price
        prev_c = info.previous_close
        # 修復 TypeError：如果抓不到數據則回傳 None
        if curr_p is None or prev_c is None:
            return None, 0, 0
        change = curr_p - prev_c
        pct = (change / prev_c) * 100
        return curr_p, change, pct
    except:
        return None, 0, 0

# --- 3. 佈局架構：先定義空容器，內容在容器內替換可防止網頁跳動 ---
# 這些變數定義在腳本開頭，確保頁面佈局不變
quote_placeholder = st.empty()
asset_placeholder = st.empty()
strategy_placeholder = st.empty()
chart_placeholder = st.empty()

# --- 4. 側邊欄控制台 ---
st.sidebar.header("🕹️ 控制台")
if st.sidebar.button("🔄 手動刷新全部圖表", type="primary"):
    st.cache_data.clear()
    st.rerun()

initial_capital = st.sidebar.number_input("初始資金 (USD)", value=31925, step=100)

with st.sidebar.form("trade"):
    st.markdown("### 📝 記帳")
    d = st.date_input("日期", date.today())
    t = st.selectbox("類別", ["買入 (Buy)", "賣出 (Sell)"])
    p = st.number_input("價格", min_value=0.0, format="%.2f")
    s = st.number_input("股數", min_value=0.0, format="%.2f")
    if st.form_submit_button("送出"):
        if save_trade(d, t, p, s):
            st.sidebar.success("已同步")
            st.cache_data.clear()
            time.sleep(1)
            st.rerun()

# --- 5. 數據處理與邏輯計算 ---
hist_data = get_nvda_analysis("NVDA")
curr_p, curr_c, curr_pct = get_realtime_data("NVDA")

# 計算台灣時間 (UTC+8)
tw_now = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')

# 如果即時抓不到，顯示最後收盤價
display_price = curr_p if curr_p is not None else (hist_data['Close'].iloc[-1] if hist_data is not None else 0)

# --- 6. 渲染各區塊 (全部填充至容器內) ---

# A. 即時報價區
with quote_placeholder.container():
    if curr_p is not None:
        p_color = '#00ff00' if curr_c >= 0 else '#ff0000'
        st.markdown(f"""
            <div style="background-color: #1e1e1e; padding: 20px; border-radius: 10px; border-left: 10px solid {p_color};">
                <h2 style="color: white; margin:0;">NVDA 即時報價: <span style="color: {p_color};">
                    ${curr_p:.2f} ({'+' if curr_c >=0 else ''}{curr_c:.2f} / {'+' if curr_c >=0 else ''}{curr_pct:.2f}%)
                </span></h2>
                <p style="color: gray; margin:0; font-size: 0.9em;">最後同步 (台北時間): {tw_now}</p>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.warning("🔄 數據更新中...")

# B. 資產概況區
trades = load_trades()
total_shares = 0
cash = initial_capital
invested_cost = 0
for _, r in trades.iterrows():
    amt = float(r['Price']) * float(r['Shares'])
    if "買入" in str(r['Type']):
        total_shares += r['Shares']
        cash -= amt
        invested_cost += amt
    else:
        total_shares -= r['Shares']
        cash += amt
        if total_shares > 0:
            invested_cost *= (1 - (r['Shares'] / (total_shares + r['Shares'])))
        else:
            invested_cost = 0

mkt_val = total_shares * display_price
avg_cost = (invested_cost / total_shares) if total_shares > 0 else 0
pl_val = (cash + mkt_val) - initial_capital
pl_pct = (pl_val / initial_capital) * 100

with asset_placeholder.container():
    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("持倉市值", f"${mkt_val:.2f}", f"{total_shares:.0f} 股")
    c2.metric("手中現金", f"${cash:.2f}")
    c3.metric("總損益", f"${pl_val:.2f}", f"{pl_pct:.2f}%")
    c4.metric("平均成本", f"${avg_cost:.2f}", f"現價 ${display_price:.2f}")

# C. 策略核心邏輯 (完全保留原 V6 交易邏輯)
with strategy_placeholder.container():
    if hist_data is not None:
        last_row = hist_data.iloc[-1]
        sma200, sma60, sma20 = last_row['SMA200'], last_row['SMA60'], last_row['SMA20']
        rsi, bb_pos, macd_hist = last_row['RSI'], last_row['BB_pos'], last_row['Hist']
        
        bull_trend = display_price > sma200
        is_oversold = rsi < (40 if bull_trend else 30)
        is_overbought = rsi > (78 if bull_trend else 70)
        is_near_lower = bb_pos < 15
        is_near_upper = bb_pos > 85
        macd_turn_up = macd_hist > hist_data['Hist'].iloc[-2]
        
        score = sum([is_oversold, is_near_lower, (macd_turn_up and last_row['MACD'] > 0), bull_trend])
        
        action = "HOLD"; shares_to_trade = 0
        pos_ratio = mkt_val / initial_capital
        trend_break = display_price < sma60 and sma20 < sma60
        bull_protect = bull_trend and (display_price > sma60)
        
        if total_shares > 0 and not bull_protect and (is_overbought or is_near_upper or trend_break):
            shares_to_trade = math.ceil(total_shares * 0.25)
            action = "SELL"
        elif cash > 0 and pos_ratio < 0.6:
            risk_f = max(0.2, 1 - abs(display_price - sma200)/sma200)
            if score >= 3:
                shares_to_trade = math.floor((cash * 0.4 * risk_f) / display_price)
                action = "STRONG_BUY"
            elif score == 2 and pos_ratio < 0.3:
                shares_to_trade = math.floor((cash * 0.2 * risk_f) / display_price)
                action = "BUY"

        st.subheader("🧠 策略訊號")
        sc1, sc2 = st.columns([1, 3])
        sc1.metric("策略建議", action, f"{shares_to_trade} 股")
        sc2.info(f"RSI: {rsi:.1f} | BB位置: {bb_pos:.1f}% | 趨勢: {'多頭' if bull_trend else '空頭'} | MACD柱: {macd_hist:.2f}")

# D. 技術分析圖表 (由於有 cache，每 2 秒刷新時不會閃爍)
with chart_placeholder.container():
    st.divider()
    st.subheader("📈 技術分析 (每小時更新數據)")
    if hist_data is not None:
        chart_df = hist_data.tail(126)
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.6,0.2,0.2])
        fig.add_trace(go.Candlestick(x=chart_df.index, open=chart_df['Open'], high=chart_df['High'], low=chart_df['Low'], close=chart_df['Close'], name='NVDA'), row=1, col=1)
        for ma, clr in zip(['SMA20','SMA60','SMA200'], ['#FFA500','#00CED1','#9400D3']):
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df[ma], line=dict(color=clr, width=1.5), name=ma), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['RSI'], line=dict(color='#9370DB', width=2), name='RSI'), row=2, col=1)
        m_colors = ['#2E8B57' if v >= 0 else '#CD5C5C' for v in chart_df['Hist']]
        fig.add_trace(go.Bar(x=chart_df.index, y=chart_df['Hist'], marker_color=m_colors, name='MACD柱'), row=3, col=1)
        fig.update_layout(height=700, xaxis_rangeslider_visible=False, template="plotly_dark", margin=dict(t=30, b=30))
        st.plotly_chart(fig, use_container_width=True)

# 顯示歷史交易
with st.expander("📋 交易紀錄"):
    if not trades.empty:
        st.dataframe(trades.sort_index(ascending=False), use_container_width=True)
