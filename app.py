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
st_autorefresh(interval=5000, limit=None, key="heartbeat")
st.title("🚀 NVDA 戰情室 V6.5（即時＋風控強化）")

# ===============================
# 1. Google Sheet
# ===============================
def get_gsheet_client():
    return gspread.service_account_from_dict(st.secrets["gcp_service_account"])

@st.cache_data(ttl=1800)
def load_trades():
    try:
        sh = get_gsheet_client().open(PORTFOLIO_SHEET_TITLE).sheet1
        df = pd.DataFrame(sh.get_all_records())
        if df.empty:
            return pd.DataFrame(columns=['Date','Type','Price','Shares','Total'])
        for c in ['Price','Shares','Total']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"交易紀錄讀取失敗：{e}")
        return pd.DataFrame(columns=['Date','Type','Price','Shares','Total'])

def save_trade(d, t, p, s):
    try:
        sh = get_gsheet_client().open(PORTFOLIO_SHEET_TITLE).sheet1
        sh.append_row([str(d), t, float(p), float(s), float(p*s)])
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

def get_realtime(symbol):
    try:
        info = yf.Ticker(symbol).fast_info
        return info.last_price, info.previous_close
    except:
        return None, None

# ===============================
# 3. Sidebar
# ===============================
st.sidebar.header("控制台")
if st.sidebar.button("🔄 強制重整"):
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
# 4. 即時報價 Header（保留）
# ===============================
hist = get_nvda_analysis("NVDA")

@st.fragment
def show_realtime_header():
    price, prev = get_realtime("NVDA")
    now = (datetime.utcnow() + timedelta(hours=8)).strftime("%H:%M:%S")

    if price is not None and prev is not None:
        chg = price - prev
        pct = chg / prev * 100
        color = "#00ff00" if chg >= 0 else "#ff4444"
        st.markdown(f"""
        <div style="background:#1e1e1e;padding:16px;border-radius:8px;border-left:5px solid {color}">
            <h2 style="color:white;margin:0;">
            NVDA ${price:.2f}
            <span style="color:{color}">
            ({chg:+.2f} / {pct:+.2f}%)
            </span></h2>
            <p style="color:gray;margin:0;">台北時間 {now}｜5 秒刷新</p>
        </div>
        """, unsafe_allow_html=True)
        return price, True

    st.warning("即時報價異常，策略凍結")
    return hist['Close'].iloc[-1], False

current_price, realtime_ok = show_realtime_header()

# ===============================
# 5. 資產計算
# ===============================
trades = load_trades()
shares, cash, cost = 0, initial_capital, 0

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

mkt_val = shares * current_price
pl_val = cash + mkt_val - initial_capital
pl_pct = pl_val / initial_capital * 100

c1, c2, c3, c4 = st.columns(4)
c1.metric("持倉市值", f"${mkt_val:,.0f}")
c2.metric("現金", f"${cash:,.0f}")
c3.metric("總損益", f"${pl_val:,.0f}", f"{pl_pct:.2f}%")
c4.metric("平均成本", f"${(cost/shares if shares>0 else 0):.2f}")

# ===============================
# 6. 策略核心（強化）
# ===============================
last = hist.iloc[-1]
prev_hist = hist['Hist'].iloc[-2]

bull = current_price > last['SMA200']
rsi_low, rsi_high = (40, 78) if bull else (30, 70)

is_oversold = last['RSI'] < rsi_low
is_overbought = last['RSI'] > rsi_high
near_low = last['BB_pos'] < 15
near_high = last['BB_pos'] > 85
macd_up = last['Hist'] > prev_hist and last['MACD'] > 0
trend_weak = last['Hist'] < prev_hist and last['MACD'] > 0

score = (
    (2.0 if bull else 0) +
    (1.5 if macd_up else 0) +
    (1.0 if is_oversold else 0) +
    (0.5 if near_low else 0)
)

action, qty = "HOLD", 0
position_ratio = mkt_val / initial_capital

if pl_pct < -15:
    action = "RISK_OFF"
    qty = math.ceil(shares * 0.5)

elif realtime_ok:
    if shares > 0 and (near_high or (is_overbought and trend_weak)):
        action = "SELL"
        qty = math.ceil(shares * 0.25)
    elif cash > 0 and position_ratio < 0.6:
        dist = abs(current_price - last['SMA200']) / last['SMA200']
        risk = max(0.15, math.exp(-3 * dist))
        if score >= 4:
            action = "STRONG_BUY"
            qty = math.floor((cash * 0.4 * risk) / current_price)
        elif score >= 2.5 and position_ratio < 0.3:
            action = "BUY"
            qty = math.floor((cash * 0.2 * risk) / current_price)

st.subheader("🧠 策略建議")
st.metric("Action", action, f"{qty} 股")

st.subheader("📌 策略判斷細節")

d1, d2, d3, d4, d5 = st.columns(5)

d1.metric("趨勢", "多頭" if bull else "空頭")
d2.metric("RSI", f"{last['RSI']:.1f}")
d3.metric("BB 位置", f"{last['BB_pos']:.1f}% (0 以下 = 跌破下軌，100 以上 = 突破上軌)")
d4.metric("MACD", "翻多" if macd_up else "未翻多")
d5.metric("Score", f"{score:.2f}")

# ===============================
# 7. 技術分析圖表（完整保留）
# ===============================
st.subheader("📈 技術分析")
df = hist.tail(126)
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    row_heights=[0.6,0.2,0.2],
    subplot_titles=("Price","RSI","MACD")
)

fig.add_trace(go.Candlestick(
    x=df.index, open=df['Open'], high=df['High'],
    low=df['Low'], close=df['Close'], name="NVDA"), 1,1)

for ma, c in zip(['SMA20','SMA60','SMA200'], ['orange','cyan','violet']):
    fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma), 1,1)

fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name="RSI"), 2,1)
fig.add_hline(y=70, line_dash="dash", row=2,col=1)
fig.add_hline(y=30, line_dash="dash", row=2,col=1)

colors = ['green' if v>=0 else 'red' for v in df['Hist']]
fig.add_trace(go.Bar(x=df.index, y=df['Hist'], marker_color=colors), 3,1)

fig.update_layout(height=700, template="plotly_dark", xaxis_rangeslider_visible=False)
st.plotly_chart(fig, use_container_width=True)

# ===============================
# 8. 交易紀錄
# ===============================
with st.expander("📋 歷史交易"):
    st.dataframe(trades, use_container_width=True)
