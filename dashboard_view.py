"""派單儀錶板畫面（Streamlit 原生元件，不產生圖片，每次重跑幾乎不花時間）。"""
CAREFLOW_BUILD = '2026.10.18-v19'
import pandas as pd
import streamlit as st


def _pct(value):
    return '—' if value is None or pd.isna(value) else f'{value:.1f}%'


def render_dashboard(D, caregiver_label=str, client_label=str):
    s = D['summary']
    st.subheader('📊 排班儀錶板')
    st.caption('回答督導最常問的問題：哪裡派不出去、為什麼、誰太累、交通有沒有吃掉工時、哪些案家老是換人。'
               '派單成功率與營收薪資請看上方 KPI。')

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('固定人選服務比例', _pct(s['primary_share']),
              help=f"已派且有固定居服員的 {s['owned']} 筆中，由固定人選本人服務 {s['primary']} 筆。越高代表客戶越少換人。")
    c2.metric('請假代班／備援調度', f"{s['leave']} ／ {s['backup']} 筆",
              help='請假代班：固定人選當天請假而由別人接。備援調度：固定人選排不進去（休息日、時段衝突、工時上限…）而由備援接。')
    c3.metric('平均每人每日工時', '—' if pd.isna(s['avg_day_hours']) else f"{s['avg_day_hours']:.1f} 小時",
              help='含轉場時間；有上班的日子才計入。')
    c4.metric('接近加班上限', f"{s['near_limit_people']} ／ {s['people']} 人",
              help='月加班時數已達上限 80% 以上（含排班期外已累積）。')
    c5.metric('轉場佔總工時', '—' if pd.isna(s['travel_share']) else f"{s['travel_share'] * 100:.1f}%",
              help='轉場工時 ÷ 總工時。越低代表路線越順。')

    tab_gap, tab_load, tab_travel, tab_stable = st.tabs(['什麼時候缺人', '人力負荷', '交通', '固定人選穩定度'])

    with tab_gap:
        _render_demand_heat(st, D)
        if s['unassigned']:
            st.markdown('---')
            st.markdown('**哪裡派不出去**')
        if s['unassigned'] == 0:
            st.success('所有服務都已派出。')
        else:
            left, right = st.columns([1, 1.2], gap='large')
            with left:
                st.markdown('**未派原因與建議處理**')
                reasons = D['reasons']
                st.dataframe(reasons, hide_index=True, width='stretch', column_config={
                    '未派筆數': st.column_config.ProgressColumn('未派筆數', min_value=0,
                                                              max_value=int(max(1, reasons['未派筆數'].max())), format='%d'),
                    '建議處理': st.column_config.TextColumn('建議處理', width='large')})
                st.caption('「涉及案家」少、筆數多＝少數案家反覆失敗，處理一個案家就能一次解掉很多筆（到上方「未派案家任務」整案處理）。')
            with right:
                st.markdown('**每日未派筆數**')
                by_day = D['by_day']
                st.bar_chart(by_day[['未派']].rename(columns={'未派': '未派筆數'}), height=220)
                worst = by_day.sort_values(['成功率', '任務數'], ascending=[True, False]).head(3)
                st.caption('成功率最低的日子：' + '、'.join(f"{d}（週{r['星期']}）{r['成功率']:.0f}%" for d, r in worst.iterrows()))

    with tab_load:
        wl = D['workload']
        if wl.empty:
            st.info('目前沒有可分析的工時資料。')
        else:
            view = wl.copy()
            view['居服員'] = view['居服員'].map(caregiver_label)
            view['加班使用率'] = (view['加班使用率'] * 100).round(1)
            st.dataframe(view, hide_index=True, width='stretch', column_config={
                '加班使用率': st.column_config.ProgressColumn('加班使用率', min_value=0, max_value=100, format='%.0f%%',
                                                              help='月加班時數 ÷ 月加班上限（含排班期外已累積）'),
                '日均工時': st.column_config.NumberColumn('日均工時', format='%.1f', help='總工時 ÷ 有上班天數，含轉場'),
                '月加班上限': st.column_config.NumberColumn('月加班上限', format='%.0f')})
            over = wl.loc[wl['狀態'] != '正常']
            if over.empty:
                st.caption('沒有人接近月加班上限。日均工時最低的人是可調度的空檔來源：')
            else:
                st.caption('需注意：' + '、'.join(f"{caregiver_label(r['居服員'])}（{r['加班使用率'] * 100:.0f}%）" for _, r in over.iterrows()))
            idle = wl.loc[wl['工作天數'] > 0].sort_values('日均工時').head(5)
            if not idle.empty:
                st.bar_chart(pd.DataFrame({'日均工時': idle['日均工時'].values}, index=idle['居服員'].map(caregiver_label)), height=200)

    with tab_travel:
        ts = D['travel_stats']
        if not ts:
            st.info('目前沒有逐段交通資料。')
        else:
            a, b, c, d, e = st.columns(5)
            a.metric('轉場段數', f"{ts['段數']:,}")
            b.metric('平均', f"{ts['平均']:.1f} 分")
            c.metric('中位數', f"{ts['中位數']:.1f} 分")
            d.metric('90% 的轉場在', f"{ts['P90']:.0f} 分內")
            e.metric('超過 30 分鐘', f"{ts['超過30分鐘']} 段", help=f"最長 {ts['最長']:.0f} 分鐘")
            st.bar_chart(D['travel_bins'].set_index('區間')[['段數']], height=220)
            st.caption('長轉場多代表同一天排了相距很遠的兩案；可把遠距離案家集中給住得近的居服員，或調整當天順序。')

    with tab_stable:
        np_ = D['nonprimary']
        if np_.empty:
            st.success('所有已派服務都由各案家的固定居服員負責。')
        else:
            st.caption('這些案家最常被換人。若「主要替代者」固定是同一人、且不是請假造成，可以考慮把他設成歷史首選或備援，下次排班就不用再臨時調度。')
            view = np_.head(15).copy()
            for col in ('固定居服員', '主要替代者'):
                view[col] = view[col].map(caregiver_label)
            view['案家ID'] = view['案家ID'].map(client_label)
            st.dataframe(view, hide_index=True, width='stretch')


def _render_demand_heat(st, D):
    """排班需求熱圖：什麼星期、哪個時段最需要人（紅＝需求高／吃緊，就多排幾位）。"""
    demand = D.get('heat_demand')
    if demand is None or demand.empty:
        st.info('排班資料沒有日期與時段，無法畫需求熱圖。')
        return
    supply, pressure = D.get('heat_supply'), D.get('heat_pressure')
    has_supply = supply is not None and not supply.empty
    modes = ['需求量（平均同時要幾位居服員）', '整段期間總服務人時']
    if has_supply:
        modes.append('吃緊度（需求 ÷ 可上班人數）')
    modes.append('未派人時（缺人的地方）')
    st.markdown('**星期 × 時段：排班需求量熱圖**（顏色越紅＝這個時段越需要人，越該多排班）')
    mode = st.radio('熱圖看什麼', modes, horizontal=True, key='dash_heat_mode', label_visibility='collapsed')
    if mode.startswith('需求量'):
        table, fmt, unit = demand, '{:.1f}', '位'
    elif mode.startswith('整段'):
        table, fmt, unit = D['heat_hours'], '{:.1f}', '人時'
    elif mode.startswith('吃緊度'):
        table, fmt, unit = pressure, '{:.0%}', ''
    else:
        table, fmt, unit = D['heat_unassigned_hours'], '{:.1f}', '人時'
    try:
        st.dataframe(table.style.background_gradient(cmap='Reds', axis=None).format(fmt), width='stretch')
    except Exception:
        st.dataframe(table, width='stretch')
    from dashboard_metrics import peak_slots
    peaks = peak_slots(table, supply if has_supply else None, demand)
    if peaks:
        def one(p):
            text = f"{p['星期']} {p['時段']}"
            if mode.startswith('吃緊度'):
                text += f"（需求 {p['需求']:.1f} 位 ÷ 可上班 {p['可上班']} 位＝{p['值']:.0%}）"
            elif mode.startswith('未派'):
                text += f"（未派 {p['值']:.1f} 人時）"
            else:
                text += f"（{p['值']:.1f}{unit}" + (f"，可上班 {p['可上班']} 位" if p['可上班'] is not None else '') + '）'
            return text
        st.caption('最紅的時段：' + '、'.join(one(p) for p in peaks)
                   + '。這些時段建議多排幾位上班（或安排備援待命）；顏色淺的時段人力較寬鬆，可以把可挪動的服務移過去。')
    st.caption('需求含已派與未派的全部服務；服務跨小時會按重疊分鐘拆開。「可上班」依居服員休息日與每日可服務時段計算，不含個別請假。')
