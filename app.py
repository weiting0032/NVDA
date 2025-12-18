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
from streamlit_autorefresh import st_autorefresh # 建議安裝此套件

# --- 0. 基礎設定 ---
PORTFOLIO_SHEET_TITLE = 'Streamlit NVDA' 
st.set_page_config(page_title="NVDA 戰情中心 V6", layout="wide")

# 每 2 秒自動刷新頁面 (僅針對即時數據部分)
count = st_autorefresh(interval=2000, limit=None, key="fizzbuzzcounter")

st.title("🚀 NVDA 戰情室 V6 (Real-time)")

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

# --- 快取歷史數據 (1小時更新一次) ---
@st.cache_data(ttl=3600)
def get_nvda_hist(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    df = stock.history(period="2y", auto_adjust=False)
    if df.empty: return None
    # 指標計算邏輯保持不變
    df['SMA20'] = df['Close'].rolling(20).mean()
    df['SMA60'] = df['Close'].rolling(60).mean()
    df['SMA200'] = df['Close'].rolling(200).mean()
    std = df['Close'].rolling(20).std()
    df['BB_upper'] = df['SMA20'] + 2 * std
    df['BB_lower'] = df['SMA20'] - 2 * std
    df['BB_pos'] = (df['Close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower']) * 100
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']
    return df

# --- 獲取即時價格 (不快取) ---
def get_realtime_price(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        # 使用 fast_info 獲取最新成交價與漲跌
        info = ticker.fast_info
        current_price = info.last_price
        prev_close = info.previous_close
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100
        return current_price, change, pct_change
    except:
        return None, 0, 0

# --- 2. 側邊欄控制台 ---
st.sidebar.header("🕹️ 控制台")
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
            st.cache_data.clear()
            time.sleep(1)
            st.rerun()

# --- 3. 獲取數據與即時看板 ---
hist = get_nvda_hist("NVDA")
curr_p, curr_c, curr_pct = get_realtime_price("NVDA")

if hist is None:
    st.error("無法取得 NVDA 數據")
    st.stop()

# 即時報價裝飾
st.markdown(f"""
    <div style="background-color: #1e1e1e; padding: 20px; border-radius: 10px; border-left: 5px solid {'#00ff00' if curr_c >= 0 else '#ff0000'};">
        <h2 style="color: white; margin:0;">NVDA 即時報價: <span style="color: {'#00ff00' if curr_c >= 0 else '#ff0000'};">
            ${curr_p:.2f} ({'+' if curr_c >=0 else ''}{curr_c:.2f} / {'+' if curr_c >=0 else ''}{curr_pct:.2f}%)
        </span></h2>
        <p style="color: gray; margin:0;">最後同步時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
""", unsafe_allow_html=True)

# --- 4. 資產計算 ---
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

# 使用即時價格更新市值
mkt_val = total_shares * (curr_p if curr_p else hist['Close'].iloc[-1])
avg_cost = (invested_cost / total_shares) if total_shares > 0 else 0
pl_val = (cash + mkt_val) - initial_capital
pl_pct = (pl_val / initial_capital) * 100

# --- 5. 策略核心 (維持原邏輯) ---
row = hist.iloc[-1]
# 注意：決策基準仍使用日線收盤，以維持穩定性
price = curr_p if curr_p else float(row['Close']) 
sma200, sma60, sma20 = row['SMA200'], row['SMA60'], row['SMA20']
rsi, bb_pos, hist_val = row['RSI'], row['BB_pos'], row['Hist']
prev_hist = hist['Hist'].iloc[-2]

bull_trend = price > sma200
score = sum([rsi < (40 if bull_trend else 30), bb_pos < 15, (hist_val > prev_hist and row['MACD'] > 0), bull_trend])

action = "HOLD"; shares_to_trade = 0
pos_ratio = mkt_val / initial_capital
trend_break = price < sma60 and sma20 < sma60
bull_protect = bull_trend and (price > sma60)

if total_shares > 0 and not bull_protect and (rsi > (78 if bull_trend else 70) or bb_pos > 85 or trend_break):
    shares_to_trade = math.ceil(total_shares * 0.25); action = "SELL"
elif cash > 0 and pos_ratio < 0.6:
    dist = abs(price - sma200)/sma200
    risk = max(0.2, 1 - dist)
    if score >= 3:
        shares_to_trade = math.floor((cash * 0.4 * risk) / price); action = "STRONG_BUY"
    elif score == 2 and pos_ratio < 0.3:
        shares_to_trade = math.floor((cash * 0.2 * risk) / price); action = "BUY"

# --- 6. 介面呈現 ---
st.divider()
c1, c2, c3, c4 = st.columns(4)
c1.metric("持倉市值", f"${mkt_val:.2f}", f"{total_shares:.0f}股")
c2.metric("手中現金", f"${cash:.2f}")
c3.metric("總損益", f"${pl_val:.2f}", f"{pl_pct:.2f}%")
c4.metric("策略建議", action, f"{shares_to_trade} 股")

# 指標概況
st.write(f"📊 **指標現況**：RSI: `{rsi:.1f}` | BB位置: `{bb_pos:.1f}%` | 趨勢: `{'多頭' if bull_trend else '空頭'}` | MACD柱: `{hist_val:.2f}`")

# --- 8. 技術分析圖表 ---
st.subheader("📈 技術分析 (Daily Context)")
chart_hist = hist.tail(126)
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.6,0.2,0.2], subplot_titles=("Price & MA","RSI","MACD"))
fig.add_trace(go.Candlestick(x=chart_hist.index, open=chart_hist['Open'], high=chart_hist['High'], low=chart_hist['Low'], close=chart_hist['Close'], name='NVDA'), row=1, col=1)
# ...其餘圖表代碼維持原狀...
for ma, col in zip(['SMA20','SMA60','SMA200'],['#FFA500','#00CED1','#9400D3']):
    fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist[ma], line=dict(color=col, width=1.5), name=ma), row=1, col=1)
fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['RSI'], line=dict(color='#9370DB', width=2), name='RSI'), row=2, col=1)
colors = ['#2E8B57' if v >= 0 else '#CD5C5C' for v in chart_hist['Hist']]
fig.add_trace(go.Bar(x=chart_hist.index, y=chart_hist['Hist'], marker_color=colors, name='MACD柱'), row=3, col=1)
fig.update_layout(height=700, xaxis_rangeslider_visible=False, template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# --- 9. 交易紀錄 ---
with st.expander("查看歷史交易紀錄"):
    if not trades.empty:
        st.dataframe(trades.sort_index(ascending=False), use_container_width=True)
