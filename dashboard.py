"""
小宇宙播客 Streamlit 看板
用法：streamlit run dashboard.py
依赖：pip install streamlit pandas plotly
"""

import glob
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── 页面配置 ──────────────────────────────────────────────
st.set_page_config(
    page_title="小宇宙播客看板",
    page_icon="🎙️",
    layout="wide",
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.6rem; }
[data-testid="stMetricLabel"] { font-size: 0.8rem; color: #888; }
</style>
""", unsafe_allow_html=True)


# ── 数据加载 ──────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "output"

PODCAST_COLORS = {
    "商业就是这样": "#378ADD",
    "硅谷101":      "#7F77DD",
    "张小珺Jùn｜商业访谈录": "#1D9E75",
}

def color_for(name: str) -> str:
    for key, col in PODCAST_COLORS.items():
        if key in name:
            return col
    colors = list(PODCAST_COLORS.values())
    return colors[hash(name) % len(colors)]


@st.cache_data(ttl=300)
def load_latest_csv(output_dir: Path) -> pd.DataFrame:
    """加载 output/ 目录下最新的 CSV 文件。"""
    pattern = str(output_dir / "xiaoyuzhou_episodes_*.csv")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return pd.DataFrame()
    df = pd.read_csv(files[0], encoding="utf-8-sig")
    df["published_at_est"] = pd.to_datetime(df["published_at_est"], errors="coerce")
    for col in ["play_count", "comment_count", "like_count", "subscribers", "duration_minutes"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["_source_file"] = Path(files[0]).name
    return df


def summary_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for podcast, part in df.groupby("podcast_title"):
        part = part.sort_values("published_at_est", ascending=False)
        freq_days = None
        if len(part) > 1:
            deltas = part["published_at_est"].sort_values(ascending=False).diff().dropna().abs()
            if not deltas.empty:
                freq_days = round(deltas.dt.days.mean(), 1)
        rows.append({
            "播客":      podcast,
            "订阅数":    int(part["subscribers"].dropna().max()) if not part["subscribers"].dropna().empty else None,
            "抓取集数":  len(part),
            "更新间隔(天)": freq_days,
            "均时长(分)": round(part["duration_minutes"].mean(), 1) if not part["duration_minutes"].dropna().empty else None,
            "均播放量":  round(part["play_count"].mean()) if not part["play_count"].dropna().empty else None,
            "均评论数":  round(part["comment_count"].mean(), 1) if not part["comment_count"].dropna().empty else None,
            "均点赞数":  round(part["like_count"].mean(), 1) if "like_count" in part and not part["like_count"].dropna().empty else None,
        })
    return pd.DataFrame(rows)


# ── 主界面 ────────────────────────────────────────────────
st.title("🎙️ 小宇宙播客竞品看板")

col_refresh, col_info = st.columns([1, 5])
with col_refresh:
    if st.button("⟳ 刷新数据"):
        st.cache_data.clear()
        st.rerun()

df = load_latest_csv(OUTPUT_DIR)

if df.empty:
    st.warning(f"未找到数据文件。请先运行爬虫：\n```\npython scrape_xiaoyuzhou.py --limit 100\n```\n输出目录：`{OUTPUT_DIR}`")
    st.stop()

with col_info:
    src = df["_source_file"].iloc[0] if "_source_file" in df.columns else "未知"
    st.caption(f"数据来源：`{src}`　｜　共 {len(df)} 条记录")

summary = summary_table(df)
podcasts = df["podcast_title"].unique().tolist()

# ── 顶部 KPI（Top 5 详细卡片 + 其余列表）────────────────────
st.divider()

ranked = summary.sort_values("订阅数", ascending=False, na_position="last").reset_index(drop=True)
top5 = ranked.head(5)
rest = ranked.iloc[5:]

st.subheader("🏆 Top 5 节目（按订阅数）")
if not top5.empty:
    kpi_cols = st.columns(len(top5))
    for i, (_, row) in enumerate(top5.iterrows()):
        subs = int(row["订阅数"]) if pd.notna(row["订阅数"]) else 0
        avg_play = int(row["均播放量"]) if pd.notna(row["均播放量"]) else 0
        freq = row["更新间隔(天)"]
        with kpi_cols[i]:
            st.metric("订阅数", f"{subs:,}")
            st.metric("均播放量", f"{avg_play:,}")
            st.metric("更新间隔", f"{freq} 天" if pd.notna(freq) else "—")
            st.caption(f"#{i + 1}　{row['播客']}")

if not rest.empty:
    st.markdown("**其余节目**")
    rest_show = rest[["播客", "订阅数", "抓取集数", "更新间隔(天)", "均播放量", "均评论数"]].copy()
    rest_show.insert(0, "排名", range(6, 6 + len(rest_show)))
    st.dataframe(
        rest_show,
        use_container_width=True,
        hide_index=True,
        column_config={
            "订阅数": st.column_config.NumberColumn(format="%d"),
            "均播放量": st.column_config.NumberColumn(format="%d"),
            "均评论数": st.column_config.NumberColumn(format="%.1f"),
        },
    )

st.divider()

# ── 图表区 ────────────────────────────────────────────────
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("订阅量对比")
    fig_sub = px.bar(
        summary.sort_values("订阅数", ascending=True),
        x="订阅数", y="播客", orientation="h",
        color="播客",
        color_discrete_map={p: color_for(p) for p in summary["播客"]},
        text="订阅数",
    )
    fig_sub.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig_sub.update_layout(showlegend=False, margin=dict(l=0, r=60, t=10, b=10), height=220)
    st.plotly_chart(fig_sub, use_container_width=True)

with chart_col2:
    st.subheader("均播放量 vs 均评论数")
    fig_cmp = go.Figure()
    for _, row in summary.iterrows():
        col = color_for(row["播客"])
        fig_cmp.add_trace(go.Bar(
            name=row["播客"], x=[row["播客"]],
            y=[row["均播放量"]], marker_color=col, legendgroup=row["播客"],
        ))
        fig_cmp.add_trace(go.Bar(
            name=row["播客"] + "_评论×100", x=[row["播客"]],
            y=[(row["均评论数"] or 0) * 100],
            marker_color=col, opacity=0.4,
            legendgroup=row["播客"], showlegend=False,
        ))
    fig_cmp.update_layout(
        barmode="group", showlegend=False,
        margin=dict(l=0, r=0, t=10, b=10), height=220,
        yaxis_title="播放量（评论×100虚线对比）",
    )
    st.plotly_chart(fig_cmp, use_container_width=True)

# ── 播放量趋势 ────────────────────────────────────────────
st.subheader("播放量趋势（按发布时间）")

# 默认展示 Top 5（按订阅数），避免一次画 15 条线过于杂乱
default_trend = ranked.head(5)["播客"].tolist()
trend_selected = st.multiselect(
    "选择要对比的播客", podcasts, default=default_trend,
    key="trend_podcasts",
    help="默认展示订阅数 Top 5，可自由增减",
)

trend_df = df.dropna(subset=["published_at_est", "play_count"]).copy()
trend_df = trend_df[trend_df["podcast_title"].isin(trend_selected)]
trend_df = trend_df.sort_values("published_at_est")

if trend_df.empty:
    st.info("请至少选择一个播客。")
else:
    fig_trend = px.line(
        trend_df, x="published_at_est", y="play_count",
        color="podcast_title",
        color_discrete_map={p: color_for(p) for p in trend_df["podcast_title"].unique()},
        markers=True, hover_data=["episode_title", "comment_count"],
        labels={"published_at_est": "发布日期", "play_count": "播放量", "podcast_title": "播客"},
    )
    fig_trend.update_layout(margin=dict(l=0, r=0, t=10, b=10), height=300, legend_title="")
    st.plotly_chart(fig_trend, use_container_width=True)

# ── 单集明细 ──────────────────────────────────────────────
st.subheader("单集明细")

filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 2])
with filter_col1:
    selected = st.multiselect("筛选播客", podcasts, default=podcasts)
with filter_col2:
    sort_by = st.selectbox("排序字段", ["published_at_est", "play_count", "comment_count", "like_count", "duration_minutes"], index=0)
with filter_col3:
    sort_asc = st.radio("排序方向", ["降序", "升序"], horizontal=True) == "升序"

filtered = df[df["podcast_title"].isin(selected)].copy()
filtered = filtered.sort_values(sort_by, ascending=sort_asc)

display_cols = ["podcast_title", "episode_title", "published_at_est",
                "duration_minutes", "play_count", "comment_count", "like_count"]
display_cols = [c for c in display_cols if c in filtered.columns]

col_rename = {
    "podcast_title": "播客",
    "episode_title": "单集标题",
    "published_at_est": "发布日期",
    "duration_minutes": "时长(分)",
    "play_count": "播放量",
    "comment_count": "评论数",
    "like_count": "点赞数",
}

show_df = filtered[display_cols].rename(columns=col_rename)
show_df["发布日期"] = show_df["发布日期"].dt.strftime("%Y-%m-%d")

st.dataframe(
    show_df,
    use_container_width=True,
    height=400,
    column_config={
        "播放量": st.column_config.NumberColumn(format="%d"),
        "评论数": st.column_config.NumberColumn(format="%d"),
        "点赞数": st.column_config.NumberColumn(format="%d"),
    }
)

st.caption(f"显示 {len(show_df)} / {len(df)} 条")

# ── 汇总表 ────────────────────────────────────────────────
with st.expander("📊 节目汇总表"):
    st.dataframe(summary, use_container_width=True, hide_index=True)
