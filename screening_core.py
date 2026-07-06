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
    weekly["MA60"] = weekly["Close"].rolling(window=60, min_periods=60).mean()
    return weekly


# --------------------------------------------------------------------------
# 四項條件檢查（可傳入 progress_callback(current, total, code, name) 回報進度）
# --------------------------------------------------------------------------

def check_condition1(etf_list, tech_etf_list, progress_callback=None):
    results = []
    all_targets = etf_list + tech_etf_list
    total = len(all_targets)
    for i, item in enumerate(all_targets):
        code, name = item["code"], item["name"]
        if progress_callback:
            progress_callback(i + 1, total, code, name)
        df = fetch_daily(code)
        if df.empty or len(df) < KD_N + 5:
            continue
        df = compute_kd(df)
        last = df.iloc[-1]
        k_val = round(float(last["K"]), 1)
        if k_val < KD_K_THRESHOLD:
            results.append({
                "code": code, "name": name,
                "desc": f"K值 {k_val}，已進入超賣區間（<{KD_K_THRESHOLD}）"
            })
    return results


def check_condition2(stock_list, progress_callback=None):
    results = []
    total = len(stock_list)
    for i, item in enumerate(stock_list):
        code, name = item["code"], item["name"]
        if progress_callback:
            progress_callback(i + 1, total, code, name)
        df = fetch_daily(code)
        if df.empty or len(df) < (60 + 2) * 5:
            continue
        weekly = resample_weekly(df)
        weekly = weekly.dropna(subset=["MA5", "MA10", "MA20", "MA60"])
        if len(weekly) < 3:
            continue

        latest = weekly.iloc[-1]
        prev = weekly.iloc[-2]

        crossed_up = (prev["Close"] < prev["MA20"]) and (latest["Close"] >= latest["MA20"])
        if not crossed_up:
            continue

        above_all_ma = (
            latest["Close"] >= latest["MA5"]
            and latest["Close"] >= latest["MA10"]
            and latest["Close"] >= latest["MA20"]
            and latest["Close"] >= latest["MA60"]
        )
        if not above_all_ma:
            continue

        if VOLUME_COMPARE_MODE == "prev_week":
            base_volume = prev["Volume"]
        else:
            base_volume = weekly["Volume"].iloc[-(VOLUME_AVG_LOOKBACK + 1):-1].mean()

        if base_volume <= 0 or pd.isna(base_volume):
            continue

        volume_change_pct = (latest["Volume"] - base_volume) / base_volume * 100

        if volume_change_pct > 0:
            results.append({
                "code": code, "name": name,
                "desc": (f"週收盤價站上週20MA，且位於MA5/10/20/60所有均線之上，本週量較"
                         f"{'前週' if VOLUME_COMPARE_MODE == 'prev_week' else '近期均量'}"
                         f"增加{volume_change_pct:.0f}%")
            })
    return results


def check_condition3(stock_list, progress_callback=None):
    results = []
    total = len(stock_list)
    for i, item in enumerate(stock_list):
        code, name = item["code"], item["name"]
        if progress_callback:
            progress_callback(i + 1, total, code, name)
        df = fetch_daily(code)
        if df.empty or len(df) < DAILY_MA_SHORT + 2:
            continue
        df = compute_daily_ma(df)
        df = df.dropna(subset=["MA20"])
        if len(df) < 2:
            continue

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        touched_today = latest["Low"] <= latest["MA20"]
        above_yesterday = prev["Close"] > prev["MA20"]

        if touched_today and above_yesterday:
            results.append({
                "code": code, "name": name,
                "desc": "當日最低價觸及/跌破日20MA，前一日仍在均線上方"
            })
    return results


def check_condition4(stock_list, progress_callback=None):
    results = []
    total = len(stock_list)
    for i, item in enumerate(stock_list):
        code, name = item["code"], item["name"]
        if progress_callback:
            progress_callback(i + 1, total, code, name)
        df = fetch_daily(code)
        if df.empty or len(df) < DAILY_MA_LONG + 2:
            continue
        df = compute_daily_ma(df)
        df = df.dropna(subset=["MA60"])
        if len(df) < 2:
            continue

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        touched_today = latest["Low"] <= latest["MA60"]
        above_yesterday = prev["Close"] > prev["MA60"]

        if touched_today and above_yesterday:
            results.append({
                "code": code, "name": name,
                "desc": "當日最低價跌破日60MA季線，前一日仍在均線上方"
            })
    return results


def format_section(title, results):
    lines = [f"【{title}】"]
    if not results:
        lines.append("本次無符合標的")
    else:
        for r in results:
            lines.append(f"• {r['code']} {r['name']} — {r['desc']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 企業補充資訊：產業分類、簡介、EPS歷史與預估
# --------------------------------------------------------------------------

def _load_twse_company_map():
    """
    下載並快取台灣證交所官方上市公司基本資料（含產業別代碼）。
    來源：TWSE OpenAPI t187ap03_L，政府開放資料，免金鑰、免費。
    回傳 dict：{公司代號: {"industry_code": "24", "company_name": "..."}}
    """
    global _TWSE_COMPANY_CACHE
    if _TWSE_COMPANY_CACHE is not None:
        return _TWSE_COMPANY_CACHE

    result = {}
    try:
        resp = requests.get(TWSE_COMPANY_INFO_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for item in data:
            code = item.get("公司代號")
            if code:
                result[code] = {
                    "industry_code": item.get("產業別", ""),
                    "company_name": item.get("公司名稱", ""),
                }
    except Exception:
        result = {}

    _TWSE_COMPANY_CACHE = result
    return result


def _get_wikipedia_summary(query: str, max_chars: int = 50):
    """
    以公司名稱查詢中文維基百科摘要，作為企業簡介的備援來源。
    來源為公開授權內容（CC BY-SA），僅取開頭摘要並截斷至指定字數。
    查無資料時回傳 None（不捏造內容）。
    """
    try:
        resp = requests.get(
            "https://zh.wikipedia.org/api/rest_v1/page/summary/" + query,
            headers={"User-Agent": "stock-screener-app"},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        extract = data.get("extract", "").strip()
        if not extract:
            return None
        return (extract[:max_chars] + "…") if len(extract) > max_chars else extract
    except Exception:
        return None


def get_stock_profile(code: str) -> dict:
    """
    取得個股補充資訊：產業分類、簡介、近3年EPS、當年度預估EPS。

    產業分類：優先使用台灣證交所官方公開資料（TWSE OpenAPI），
             查無資料時退回 yfinance 的 sector/industry 欄位。
    企業簡介：優先使用中文維基百科摘要（來源：CC BY-SA授權），
             查無資料時退回 yfinance 的英文簡介。
    EPS：來自 yfinance 財報資料，覆蓋率有限。
    任何欄位查無資料時會標示「資料不足」，不會捏造內容。
    """
    ticker_str = to_yf_ticker(code)
    profile = {
        "industry": "資料不足",
        "summary": "資料不足",
        "summary_source": "",
        "eps_history": [],
        "eps_forecast": None,
        "eps_forecast_note": "",
    }

    # --- 產業分類：優先用TWSE官方資料 ---
    company_map = _load_twse_company_map()
    company_entry = company_map.get(code)
    company_name = None
    if company_entry:
        company_name = company_entry.get("company_name")
        industry_code = company_entry.get("industry_code", "")
        industry_label = TWSE_INDUSTRY_MAP.get(industry_code)
        if industry_label:
            profile["industry"] = f"{industry_label}（證交所官方分類）"

    # --- 企業簡介：優先用中文維基百科 ---
    if company_name:
        wiki_summary = _get_wikipedia_summary(company_name)
        if wiki_summary:
            profile["summary"] = wiki_summary
            profile["summary_source"] = "資料來源：中文維基百科（CC BY-SA授權）"

    try:
        ticker = yf.Ticker(ticker_str)
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            info = {}

        # 產業分類備援：yfinance
        if profile["industry"] == "資料不足":
            industry = info.get("industry") or info.get("sector")
            if industry:
                profile["industry"] = f"{industry}（Yahoo Finance，可能為英文）"

        # 企業簡介備援：yfinance（通常為英文）
        if profile["summary"] == "資料不足":
            summary = info.get("longBusinessSummary")
            if summary:
                summary = summary.strip()
                profile["summary"] = (summary[:50] + "…") if len(summary) > 50 else summary
                profile["summary_source"] = "資料來源：Yahoo Finance（原文為英文）"

        # 當年度／未來12個月 EPS 預估（分析師共識）
        forward_eps = info.get("forwardEps")
        if forward_eps is not None:
            profile["eps_forecast"] = round(float(forward_eps), 2)
            profile["eps_forecast_note"] = "分析師預估未來12個月EPS（非嚴格對應單一會計年度）"

        # 近3年EPS：從年度財報的 Diluted EPS / Basic EPS 取得
        try:
            income_stmt = ticker.income_stmt
            if income_stmt is not None and not income_stmt.empty:
                eps_row = None
                for row_name in ["Diluted EPS", "Basic EPS"]:
                    if row_name in income_stmt.index:
                        eps_row = income_stmt.loc[row_name]
                        break
                if eps_row is not None:
                    eps_row = eps_row.dropna().sort_index()
                    recent = eps_row.tail(3)
                    for date_idx, val in recent.items():
                        year_label = str(date_idx.year) if hasattr(date_idx, "year") else str(date_idx)
                        profile["eps_history"].append({
                            "year": year_label,
                            "eps": round(float(val), 2)
                        })
        except Exception:
            pass

    except Exception:
        pass

    return profile
