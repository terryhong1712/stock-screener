# -*- coding: utf-8 -*-
"""
台股四條件篩選 — 核心邏輯模組
================================
純運算邏輯，不含使用者互動介面。
供 stock_screener.py（終端機版）與 streamlit_app.py（網頁版）共用。
"""

import json
import os
import warnings
from datetime import datetime

import pandas as pd
import requests

warnings.filterwarnings("ignore")

import yfinance as yf

# --------------------------------------------------------------------------
# 設定
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "watchlist.json")

# TWSE OpenAPI：上市公司基本資料（含產業別代碼），官方免費資料，無需金鑰
TWSE_COMPANY_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

# 證交所產業別代碼對照表（標準分類）
TWSE_INDUSTRY_MAP = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "13": "電子工業",
    "14": "建材營造", "15": "航運業", "16": "觀光事業", "17": "金融保險",
    "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學工業",
    "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業",
    "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業",
    "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業",
    "34": "電子商務", "80": "管理股票", "91": "存託憑證",
    "97": "綠能環保", "99": "其他",
}

# 全域快取，避免每次查詢個股都重新下載整份上市公司清單
_TWSE_COMPANY_CACHE = None

# KD 參數
KD_N = 9

# 均線參數
DAILY_MA_SHORT = 20
DAILY_MA_LONG = 60
WEEKLY_MA = 20

# 條件二：成交量比較基準
VOLUME_COMPARE_MODE = "prev_week"
VOLUME_AVG_LOOKBACK = 4

# 條件一 K 值門檻
KD_K_THRESHOLD = 30

# yfinance 下載參數
DAILY_PERIOD = "1y"


# --------------------------------------------------------------------------
# 工具函式
# --------------------------------------------------------------------------

def load_watchlist():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["etf_list"], data["tech_etf_list"], data["stock_list"]


def to_yf_ticker(code: str) -> str:
    if code.endswith(".TW") or code.endswith(".TWO"):
        return code
    return f"{code}.TW"


def fetch_daily(code: str) -> pd.DataFrame:
    ticker = to_yf_ticker(code)
    try:
        df = yf.download(ticker, period=DAILY_PERIOD, interval="1d",
                          progress=False, auto_adjust=False, threads=False)
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna(subset=["Close"])
    return df


def compute_kd(df: pd.DataFrame, n=KD_N) -> pd.DataFrame:
    df = df.copy()
    low_min = df["Low"].rolling(window=n, min_periods=n).min()
    high_max = df["High"].rolling(window=n, min_periods=n).max()
    rsv = (df["Close"] - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50)

    k_values = []
    d_values = []
    prev_k, prev_d = 50.0, 50.0
    for val in rsv:
        cur_k = prev_k * (2 / 3) + val * (1 / 3)
        cur_d = prev_d * (2 / 3) + cur_k * (1 / 3)
        k_values.append(cur_k)
        d_values.append(cur_d)
        prev_k, prev_d = cur_k, cur_d

    df["K"] = k_values
    df["D"] = d_values
    return df


def compute_daily_ma(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA20"] = df["Close"].rolling(window=DAILY_MA_SHORT, min_periods=DAILY_MA_SHORT).mean()
    df["MA60"] = df["Close"].rolling(window=DAILY_MA_LONG, min_periods=DAILY_MA_LONG).mean()
    return df


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    weekly = df.resample("W-FRI").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["Close"])
    weekly["MA5"] = weekly["Close"].rolling(window=5, min_periods=5).mean()
    weekly["MA10"] = weekly["Close"].rolling(window=10, min_periods=10).mean()
    weekly["MA20"] = weekly["Close"].rolling(window=WEEKLY_MA, min_periods=WEEKLY_MA).mean()
    weekly["MA60"] = weekly["Close"].rolling(window=60,
