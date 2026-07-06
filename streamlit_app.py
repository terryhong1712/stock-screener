# -*- coding: utf-8 -*-
"""
台股四條件篩選 — 網頁版（Streamlit）
======================================
部署到 Streamlit Community Cloud 後，即可用瀏覽器打開網址，
點擊「推薦股票」按鈕觸發篩選，不需要在自己電腦裝任何東西。
"""

import streamlit as st
from datetime import datetime

from screening_core import (
    load_watchlist,
    check_condition1,
    check_condition2,
    check_condition3,
    check_condition4,
    get_stock_profile,
)

st.set_page_config(page_title="台股四條件選股", page_icon="📈", layout="centered")

st.title("📈 台股四條件自動篩選")
st.caption("資料來源：Yahoo Finance（yfinance） ｜ 手動觸發，非即時自動監控")

with st.expander("四項通報條件說明"):
    st.markdown("""
- **條件一**：大盤／ETF（0050、006208、科技型ETF）日KD之K值 < 30
- **條件二**：個股週K站上週20MA、且位於MA5/10/20/60所有均線之上，並成交量較前週增加
- **條件三**：個股日K最低價觸及/跌破日20MA（前一日仍在均線上方）
- **條件四**：個股日K最低價觸及/跌破日60MA季線（前一日仍在均線上方）

個股符合條件時，會額外顯示產業分類、簡介、近3年EPS與當年度預估EPS
（資料來源：Yahoo Finance，台股資料覆蓋率有限，查無資料會標示「資料不足」）
""")

if st.button("🔍 推薦股票", type="primary", use_container_width=True):
    etf_list, tech_etf_list, stock_list = load_watchlist()

    status = st.empty()
    progress_bar = st.progress(0)

    def make_progress_callback(label, total_stages, stage_index):
        def callback(current, total, code, name):
            overall = (stage_index + current / total) / total_stages
            progress_bar.progress(min(overall, 1.0))
            status.text(f"{label}：正在檢查 {code} {name}（{current}/{total}）")
        return callback

    total_stages = 4

    status.text("正在檢查條件一（大盤/ETF KD < 30）...")
    r1 = check_condition1(etf_list, tech_etf_list,
                           make_progress_callback("條件一", total_stages, 0))

    r2 = check_condition2(stock_list,
                           make_progress_callback("條件二", total_stages, 1))

    r3 = check_condition3(stock_list,
                           make_progress_callback("條件三", total_stages, 2))

    r4 = check_condition4(stock_list,
                           make_progress_callback("條件四", total_stages, 3))

    progress_bar.progress(1.0)
    status.text("篩選完成！")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.success(f"篩選完成，產生時間：{now_str}")

    def render_section(title, results, show_profile=False):
        st.subheader(title)
        if not results:
            st.info("本次無符合標的")
            return
        for r in results:
            st.markdown(f"**{r['code']} {r['name']}** — {r['desc']}")
            if show_profile:
                with st.spinner(f"正在查詢 {r['code']} {r['name']} 的企業資訊..."):
                    profile = get_stock_profile(r['code'])
                with st.container(border=True):
                    st.markdown(f"**產業分類：** {profile['industry']}")
                    st.markdown(f"**企業簡介：** {profile['summary']}")

                    if profile["eps_history"]:
                        eps_text = "、".join(
                            [f"{item['year']}年 {item['eps']}元" for item in profile["eps_history"]]
                        )
                        st.markdown(f"**近3年EPS：** {eps_text}")
                    else:
                        st.markdown("**近3年EPS：** 資料不足")

                    if profile["eps_forecast"] is not None:
                        st.markdown(
                            f"**當年度預估EPS：** {profile['eps_forecast']}元"
                            f"（{profile['eps_forecast_note']}）"
                        )
                    else:
                        st.markdown("**當年度預估EPS：** 資料不足")

    render_section("條件一：大盤/ETF KD < 30", r1)
    render_section("條件二：週K站上週20MA且量增", r2, show_profile=True)
    render_section("條件三：日K跌至日20MA", r3, show_profile=True)
    render_section("條件四：日K跌至日60MA", r4, show_profile=True)

    # 提供下載報告
    lines = [f"股票篩選報告\n產生時間：{now_str}\n" + "=" * 40, ""]
    for title, results in [
        ("條件一：大盤/ETF KD < 30", r1),
        ("條件二：週K站上週20MA且量增", r2),
        ("條件三：日K跌至日20MA", r3),
        ("條件四：日K跌至日60MA", r4),
    ]:
        lines.append(f"【{title}】")
        if not results:
            lines.append("本次無符合標的")
        else:
            for r in results:
                lines.append(f"• {r['code']} {r['name']} — {r['desc']}")
        lines.append("")

    st.download_button(
        "📄 下載報告 (.txt)",
        data="\n".join(lines),
        file_name=f"stock_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mime="text/plain",
    )
else:
    st.info("點擊上方「推薦股票」按鈕開始篩選（約需1-3分鐘，視標的數量而定）")
