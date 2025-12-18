import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta
import math
import gspread
import time
from streamlit_autorefresh import st_autorefresh

# ===============================
# 0. 基礎設定
# ===============================
PORTFOLIO_SHEET_TITLE = 'Streamlit NVDA'
st.set_page_config(page_title="NVDA 戰情中心 V6.5", layout="wide")
st_autorefresh(interval=5000, limit=None, key="nvda_heartbeat")
st.title("NVDA 戰情室 V6.5（風控強化版）")

# ===============================
# 1. Google Sheet
# ===============================
def get_gsheet_client():
    credentials = st.secrets["gcp_service_account"]
    return gspread.service_account_from_dict(credentials)

@st.cache_data(ttl=1800)
def load_trades():
    try:
        gc = get_gsheet_client()
        sh = gc.open(PORTFOLIO_SHEET_TITLE).sheet1
        data = sh.get_all_records()
        if not data:
            return pd.DataFrame(columns=['Date', 'Type', 'Price', 'Shares', 'Total'])
        df = pd.DataFrame(data)
        for c in ['Price', 'Shares', 'Total']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"交易紀錄讀取失敗：{e}")
        return pd.DataFrame(columns=['Date', 'Type', 'Price', 'Shares', 'Total'])

def save_trade(date_val, trans_type, price, shares):
    try:
        gc = get_gsheet_client()
        sh = gc.open(PORTFOLIO_SHEET_TITLE).sheet1
        sh.append_row([str(date_val), trans_type, float(price), float(shares), float(price*shares)])
        return True
    except Exception as e:
        st.error(f"寫入失敗：{e}")
        return False

# ===============================
# 2. 技術指標
# ===============================
@st.cache_data(ttl=600)
def get_nvda_analysis(symbol):
    df = yf.Ticker(symbol).history(period="2y")
    if df.empty:
        return None

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
    rs = gain / (loss + 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))

    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']

    return df

def get_realtime_price(symbol):
    try:
        info = yf.Ticker(symbol).fast_info
        return info.last_price, info.previous_close
    except:
        return None, None

# ===============================
# 3. 側邊欄
# ===============================
st.sidebar.header("控制台")
if st.sidebar.button("強制重整"):
    st.cache_data.clear()
    st.rerun()

initial_capital = st.sidebar.number_input("初始資金", value=31925, step=100)

with st.sidebar.form("trade"):
    d = st.date_input("日期", date.today())
    t = st.selectbox("類型", ["買入 (Buy)", "賣出 (Sell)"])
    p = st.number_input("價格", min_value=0.0, format="%.2f")
    s = st.number_input("股數", min_value=0.0, format="%.2f")
    if st.form_submit_button("送出"):
        if save_trade(d, t, p, s):
            st.success("已同步")
            st.cache_data.clear()
            time.sleep(1)
            st.rerun()

# ===============================
# 4. 即時報價
# ===============================
hist = get_nvda_analysis("NVDA")
price, prev = get_realtime_price("NVDA")

realtime_ok = price is not None and prev is not None
if not realtime_ok:
    st.warning("即時報價異常，策略暫停")
    price = hist['Close'].iloc[-1]

# ===============================
# 5. 資產計算
# ===============================
trades = load_trades()
shares = 0
cash = initial_capital
cost = 0

for _, r in trades.iterrows():
    amt = r['Price'] * r['Shares']
    if "買入" in r['Type']:
        shares += r['Shares']
        cash -= amt
        cost += amt
    else:
        shares -= r['Shares']
        cash += amt
        cost *= max(0, 1 - r['Shares'] / max(shares + r['Shares'], 1))

mkt_val = shares * price
pl_val = cash + mkt_val - initial_capital
pl_pct = pl_val / initial_capital * 100

c1, c2, c3, c4 = st.columns(4)
c1.metric("市值", f"${mkt_val:.0f}")
c2.metric("現金", f"${cash:.0f}")
c3.metric("總損益", f"${pl_val:.0f}", f"{pl_pct:.2f}%")
c4.metric("均價", f"${(cost/shares if shares>0 else 0):.2f}")

# ===============================
# 6. 策略核心（強化版）
# ===============================
last = hist.iloc[-1]
prev_hist = hist['Hist'].iloc[-2]

bull = price > last['SMA200']
rsi_low = 40 if bull else 30
rsi_high = 78 if bull else 70

is_oversold = last['RSI'] < rsi_low
is_overbought = last['RSI'] > rsi_high
near_low = last['BB_pos'] < 15
near_high = last['BB_pos'] > 85
macd_up = last['Hist'] > prev_hist and last['MACD'] > 0
trend_weak = last['Hist'] < prev_hist and last['MACD'] > 0

# --- 權重化 Score ---
score = 0
score += 2.0 if bull else 0
score += 1.5 if macd_up else 0
score += 1.0 if is_oversold else 0
score += 0.5 if near_low else 0

action = "HOLD"
qty = 0
position_ratio = mkt_val / initial_capital

# --- 最大回撤風控 ---
if pl_pct < -15:
    action = "RISK_OFF"
    qty = math.ceil(shares * 0.5)

elif realtime_ok:
    if shares > 0 and (near_high or (is_overbought and trend_weak)):
        action = "SELL"
        qty = math.ceil(shares * 0.25)
    elif cash > 0 and position_ratio < 0.6:
        dist = abs(price - last['SMA200']) / last['SMA200']
        risk = max(0.15, math.exp(-3 * dist))
        if score >= 4:
            action = "STRONG_BUY"
            qty = math.floor((cash * 0.4 * risk) / price)
        elif score >= 2.5 and position_ratio < 0.3:
            action = "BUY"
            qty = math.floor((cash * 0.2 * risk) / price)

# ===============================
# 7. UI 顯示
# ===============================
st.subheader("策略建議")
st.metric("Action", action, f"{qty} 股")

with st.expander("策略判斷細節"):
    st.json({
        "多頭趨勢": bull,
        "RSI": round(last['RSI'],1),
        "BB位置": round(last['BB_pos'],1),
        "MACD翻多": macd_up,
        "Score": score
    })
