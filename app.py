import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date
import os
import math
import gspread
import time

# --- 0. 基礎設定 ---
PORTFOLIO_SHEET_TITLE = 'Streamlit NVDA' 
st.set_page_config(page_title="NVDA 戰情中心 V6", layout="wide")
st.title("🚀 NVDA 戰情室 V6 Google Sheets 版")

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

# --- 新增：快取股票數據函數，避免 YFRateLimitError ---
@st.cache_data(ttl=3600)  # 快取 1 小時 (3600秒)
def get_nvda_data(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    # 使用 2y 資料以計算 SMA200
    df = stock.history(period="2y", auto_adjust=False)
    
    if df.empty:
        return None
        
    # --- 計算指標 (維持原邏輯) ---
    df['SMA20'] = df['Close'].rolling(20).mean()
    df['SMA60'] = df['Close'].rolling(60).mean()
    df['SMA200'] = df['Close'].rolling(200).mean()

    # Bollinger Bands
    std = df['Close'].rolling(20).std()
    df['BB_upper'] = df['SMA20'] + 2 * std
    df['BB_lower'] = df['SMA20'] - 2 * std
    df['BB_pos'] = (df['Close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower']) * 100

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

# --- 2. 側邊欄控制台 ---
st.sidebar.header("🕹️ 控制台")
if st.sidebar.button("🔄 刷新數據", type="primary"):
    st.cache_data.clear() # 強制清除快取重新抓取
    st.rerun()
st.sidebar.caption(f"最後更新: {datetime.now().strftime('%H:%M:%S')}")

st.sidebar.header("💰 資金設定")
initial_capital = st.sidebar.number_input("初始資金 (USD)", value=31925, step=100)

with st.sidebar.form("trade"):
    st.markdown("### 📝 記帳")
    d = st.date_input("日期", date.today())
    t = st.selectbox("類別", ["買入 (Buy)", "賣出 (Sell)"])
    p = st.number_input("價格", min_value=0.0, format="%.2f")
    s = st.number_input("股數", min_value=0.0, format="%.2f")
    if st.form_submit_button("送出"):
        if save_trade(d, t, p, s):
            st.sidebar.success("已同步至 Google Sheets")
            time.sleep(1)
            st.rerun()

# --- 3. 獲取數據 ---
hist = get_nvda_data("NVDA")

if hist is None or hist.empty:
    st.error("無法取得 NVDA 數據，請稍後再試或檢查網路。")
    st.stop()

# --- 4. 資產計算 (維持原邏輯) ---
trades = load_trades()
total_shares = 0
cash = initial_capital
invested_cost = 0

for i, r in trades.iterrows():
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

mkt_val = total_shares * hist['Close'].iloc[-1]
avg_cost = (invested_cost / total_shares) if total_shares > 0 else 0
pl_val = (cash + mkt_val) - initial_capital
pl_pct = (pl_val / initial_capital) * 100

# --- 5. 策略核心 (整合 V6 條件) ---
row = hist.iloc[-1]
price = float(row['Close'])
sma200 = float(row['SMA200'])
sma60 = float(row['SMA60'])
sma20 = float(row['SMA20'])
rsi = float(row['RSI'])
bb_pos = float(row['BB_pos'])
hist_val = float(row['Hist'])
prev_hist = float(hist['Hist'].iloc[-2])

bull_trend = price > sma200
oversold_rsi = 40 if bull_trend else 30
overbought_rsi = 78 if bull_trend else 70

is_oversold = rsi < oversold_rsi
is_overbought = rsi > overbought_rsi
is_near_lower = bb_pos < 15
is_near_upper = bb_pos > 85
macd_turn_up = hist_val > prev_hist
macd_above_zero = float(row['MACD']) > 0
strong_macd = macd_turn_up and macd_above_zero

score = 0
score += 1 if is_oversold else 0
score += 1 if is_near_lower else 0
score += 1 if strong_macd else 0
score += 1 if bull_trend else 0

action = "HOLD"
shares_to_trade = 0
position_ratio = mkt_val / initial_capital

# 防守賣出
trend_break = price < sma60 and sma20 < sma60
bull_protect = bull_trend and (price > sma60) 
if total_shares > 0 and not bull_protect and (is_overbought or is_near_upper or trend_break):
    shares_to_trade = math.ceil(total_shares * 0.25)
    action = "SELL"

# 買進
elif cash > 0 and position_ratio < 0.6:
    distance = abs(price - sma200)/sma200
    risk_factor = max(0.2, 1 - distance)
    if score >= 3:
        budget = cash * 0.4 * risk_factor
        shares_to_trade = math.floor(budget / price)
        action = "STRONG_BUY"
    elif score == 2 and position_ratio < 0.3:
        budget = cash * 0.2 * risk_factor
        shares_to_trade = math.floor(budget / price)
        action = "BUY"

# --- 6. 顯示策略結果 ---
st.subheader("🧠 策略訊號")
st.metric("策略建議", action, f"{shares_to_trade} 股")
st.write(f"RSI: {rsi:.1f}, BB位置: {bb_pos:.1f}%, 趨勢: {'多頭' if bull_trend else '空頭'}, MACD柱: {hist_val:.2f}")

# --- 7. 顯示資產概況 ---
st.subheader("🏦 資產概況")
c1, c2, c3, c4 = st.columns(4)
c1.metric("持倉市值", f"${mkt_val:.2f}", f"{total_shares:.0f}股")
c2.metric("手中現金", f"${cash:.2f}")
c3.metric("總損益", f"${pl_val:.2f}", f"{pl_pct:.2f}%")
c4.metric("平均成本", f"${avg_cost:.2f}", f"現價 ${price:.2f}")

# --- 8. 技術分析圖表 ---
st.subheader("📈 技術分析圖表")
chart_hist = hist.tail(126)
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.6,0.2,0.2], subplot_titles=("股價 & 均線","RSI","MACD"))
fig.add_trace(go.Candlestick(x=chart_hist.index, open=chart_hist['Open'], high=chart_hist['High'], low=chart_hist['Low'], close=chart_hist['Close'], name='NVDA'), row=1, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['SMA20'], line=dict(color='#FFA500', width=1.5), name='SMA20'), row=1, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['SMA60'], line=dict(color='#00CED1', width=1.5), name='SMA60'), row=1, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['SMA200'], line=dict(color='#9400D3', width=2), name='SMA200'), row=1, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['RSI'], line=dict(color='#9370DB', width=2), name='RSI'), row=2, col=1)
fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
colors = ['#2E8B57' if v >= 0 else '#CD5C5C' for v in chart_hist['Hist']]
fig.add_trace(go.Bar(x=chart_hist.index, y=chart_hist['Hist'], marker_color=colors, name='MACD柱'), row=3, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['MACD'], line=dict(color='#FF8C00', width=1), name='DIF'), row=3, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['Signal'], line=dict(color='#1E90FF', width=1), name='DEA'), row=3, col=1)
fig.update_layout(height=800, xaxis_rangeslider_visible=False, showlegend=True)
st.plotly_chart(fig, use_container_width=True)

# --- 9. 交易紀錄 ---
st.subheader("📋 交易紀錄 (Google Sheets)")
if not trades.empty:
    st.dataframe(trades.sort_index(ascending=False), use_container_width=True)
