import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
API_BASE = "https://api.xiaoyuzhoufm.com"


def load_env_file(path: Path = Path(".env")) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip("'").strip('"')
    return data


def to_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_relative_time(value: str, now: datetime) -> Optional[datetime]:
    value = value.strip()
    patterns = [
        (r"(\d+)小时前", "hours"),
        (r"(\d+)天前", "days"),
        (r"(\d+)个月前", "months"),
        (r"(\d+)年前", "years"),
    ]
    for pattern, unit in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        amount = int(match.group(1))
        if unit == "hours":
            return now - timedelta(hours=amount)
        if unit == "days":
            return now - timedelta(days=amount)
        if unit == "months":
            return now - timedelta(days=30 * amount)
        if unit == "years":
            return now - timedelta(days=365 * amount)
    return None


def split_play_comment_numbers(raw: str) -> Dict[str, Optional[int]]:
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return {"plays": None, "comments": None}
    if len(digits) <= 2:
        return {"plays": None, "comments": to_int(digits)}
    for comment_len in [1, 2, 3, 4]:
        if len(digits) <= comment_len:
            continue
        comment_part = digits[-comment_len:]
        play_part = digits[:-comment_len]
        comments = to_int(comment_part)
        plays = to_int(play_part)
        if comments is None or plays is None:
            continue
        # 互动评论一般远小于播放数，做一个常识过滤。
        if comments <= 5000 and plays >= comments:
            return {"plays": plays, "comments": comments}
    return {"plays": to_int(digits), "comments": None}


def parse_duration_minutes(text: str) -> Optional[int]:
    match = re.search(r"(\d+)分钟", text)
    if not match:
        return None
    return int(match.group(1))


def extract_pid_from_url(url: str) -> str:
    match = re.search(r"/podcast/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError(f"无法从链接中提取 pid: {url}")
    return match.group(1)


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_api_headers(
    token: str,
    device_id: Optional[str],
    app_version: str = "2.68",
    app_build: str = "1858",
) -> Dict[str, str]:
    """与手机 App 抓包一致的请求头（含 device-id，避免 401）。"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    headers = {
        "Host": "api.xiaoyuzhoufm.com",
        "User-Agent": f"Xiaoyuzhou/{app_version} (build:{app_build}; iOS 26.3.0)",
        "Market": "AppStore",
        "App-BuildNo": app_build,
        "OS": "ios",
        "OS-Version": "26.3.0",
        "Manufacturer": "Apple",
        "BundleID": "app.podcast.cosmos",
        "Model": "iPhone14,5",
        "app-permissions": "0",
        "Accept": "*/*",
        "Content-Type": "application/json",
        "App-Version": app_version,
        "WifiConnected": "true",
        "x-jike-access-token": token,
        "x-custom-xiaoyuzhou-app-dev": "",
        "Local-Time": now,
        "Timezone": "Asia/Shanghai",
        "abtest-info": "{}",
        "Accept-Language": "zh-Hans-CN;q=1.0",
    }
    if device_id:
        headers["x-jike-device-id"] = device_id
    return headers


def api_get(path: str, token: str, device_id: Optional[str], app_version: str, app_build: str) -> Dict:
    url = f"{API_BASE}{path}"
    response = requests.get(
        url,
        headers=build_api_headers(token, device_id, app_version, app_build),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def api_post(
    path: str, token: str, device_id: Optional[str], payload: Dict, app_version: str, app_build: str
) -> Dict:
    url = f"{API_BASE}{path}"
    response = requests.post(
        url,
        json=payload,
        headers=build_api_headers(token, device_id, app_version, app_build),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def unwrap_api_payload(payload: Dict) -> Dict:
    data = payload.get("data", payload)
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)):
        return data
    return {"data": data}


def fetch_podcast_by_api(
    token: str, device_id: str, url: str, limit: int, app_version: str, app_build: str
) -> Dict:
    pid = extract_pid_from_url(url)
    detail_raw = api_get(f"/v1/podcast/get?pid={pid}", token, device_id, app_version, app_build)
    detail_block = unwrap_api_payload(detail_raw)
    detail = detail_block.get("data", {})
    if isinstance(detail, dict) and "data" in detail:
        detail = detail["data"]
    podcast_title = detail.get("title") or pid
    subscribers = to_int(detail.get("subscriptionCount"))

    rows: List[Dict] = []
    load_more_key = None
    while len(rows) < limit:
        body = {"pid": pid, "order": "desc", "limit": min(20, limit - len(rows))}
        if load_more_key:
            body["loadMoreKey"] = load_more_key
        page_raw = api_post(
            "/v1/episode/list", token, device_id, body, app_version, app_build
        )
        page = unwrap_api_payload(page_raw)
        items = page.get("data", []) or []
        if not items:
            break
        for ep in items:
            duration_sec = to_int(ep.get("duration"))
            rows.append(
                {
                    "podcast_title": podcast_title,
                    "podcast_url": url,
                    "episode_title": ep.get("title"),
                    "published_relative": None,
                    "published_at_est": parse_datetime(ep.get("pubDate")),
                    "duration_minutes": round(duration_sec / 60) if duration_sec else None,
                    "play_count": to_int(ep.get("playCount")),
                    "comment_count": to_int(ep.get("commentCount")),
                    "like_count": to_int(ep.get("clapCount")),
                    "completion_rate": None,
                    "data_source": "direct_api",
                }
            )
            if len(rows) >= limit:
                break
        # loadMoreKey 在原始响应的顶层，unwrap 后会丢失，需从 page_raw 读取
        load_more_key = page_raw.get("loadMoreKey") or page.get("loadMoreKey")
        if not load_more_key:
            break

    return {
        "podcast_title": podcast_title,
        "podcast_url": url,
        "subscribers": subscribers,
        "episodes": rows,
    }


def xyz_post(base_url: str, token: str, endpoint: str, payload: Dict) -> Dict:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    response = requests.post(
        url,
        json=payload,
        timeout=20,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "x-jike-access-token": token,
        },
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200:
        raise ValueError(f"xyz 接口异常: {endpoint}, code={data.get('code')}, msg={data.get('msg')}")
    return data.get("data", {})


def fetch_podcast_by_xyz(base_url: str, token: str, url: str, limit: int) -> Dict:
    pid = extract_pid_from_url(url)
    detail = xyz_post(base_url, token, "/podcast_detail", {"pid": pid}).get("data", {})
    podcast_title = detail.get("title") or pid
    subscribers = to_int(detail.get("subscriptionCount"))

    rows: List[Dict] = []
    load_more_key = None
    while len(rows) < limit:
        payload = {"pid": pid, "order": "desc"}
        if load_more_key:
            payload["loadMoreKey"] = load_more_key
        page = xyz_post(base_url, token, "/episode_list", payload)
        items = page.get("data", []) or []
        if not items:
            break
        for ep in items:
            duration_sec = to_int(ep.get("duration"))
            rows.append(
                {
                    "podcast_title": podcast_title,
                    "podcast_url": url,
                    "episode_title": ep.get("title"),
                    "published_relative": None,
                    "published_at_est": parse_datetime(ep.get("pubDate")),
                    "duration_minutes": round(duration_sec / 60) if duration_sec else None,
                    "play_count": to_int(ep.get("playCount")),
                    "comment_count": to_int(ep.get("commentCount")),
                    "like_count": to_int(ep.get("clapCount")),
                    "completion_rate": None,
                    "data_source": "xyz_api",
                }
            )
            if len(rows) >= limit:
                break
        load_more_key = page.get("loadMoreKey")
        if not load_more_key:
            break

    return {
        "podcast_title": podcast_title,
        "podcast_url": url,
        "subscribers": subscribers,
        "episodes": rows,
    }


def fetch_podcast_from_next_data(url: str, limit: int, timeout: int = 20) -> Optional[Dict]:
    """从小宇宙网页 __NEXT_DATA__ 解析（无需 token，通常约 15 集/页）。"""
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        response.text,
    )
    if not match:
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text)
    if not match:
        return None

    payload = json.loads(match.group(1))
    podcast = payload.get("props", {}).get("pageProps", {}).get("podcast", {})
    if not podcast:
        return None

    title = podcast.get("title") or url
    subscribers = to_int(podcast.get("subscriptionCount"))
    episodes_raw = podcast.get("episodes") or []

    rows: List[Dict] = []
    for ep in episodes_raw[:limit]:
        duration_sec = to_int(ep.get("duration"))
        rows.append(
            {
                "podcast_title": title,
                "podcast_url": url,
                "episode_title": ep.get("title"),
                "published_relative": None,
                "published_at_est": parse_datetime(ep.get("pubDate")),
                "duration_minutes": round(duration_sec / 60) if duration_sec else None,
                "play_count": to_int(ep.get("playCount")),
                "comment_count": to_int(ep.get("commentCount")),
                "like_count": to_int(ep.get("clapCount")),
                "completion_rate": None,
                "data_source": "web_next_data",
            }
        )

    return {
        "podcast_title": title,
        "podcast_url": url,
        "subscribers": subscribers,
        "episodes": rows,
    }


def fetch_page_text(url: str, timeout: int = 20) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return soup.get_text("\n", strip=True)


def parse_podcast(text: str, url: str, now: datetime) -> Dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else url
    if title.endswith(" | 小宇宙 - 听播客，上小宇宙"):
        title = title.replace(" | 小宇宙 - 听播客，上小宇宙", "").strip()

    sub_match = re.search(r"(\d[\d,]*)已订阅", text)
    subscribers = to_int(sub_match.group(1)) if sub_match else None

    episodes: List[Dict] = []
    for line in lines:
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        duration = parse_duration_minutes(item)
        rel_match = re.search(r"(\d+小时?前|\d+天前|\d+个月前|\d+年前)", item)
        relative_time = rel_match.group(1) if rel_match else None
        published_at = parse_relative_time(relative_time, now) if relative_time else None

        title_part = item
        if duration is not None:
            split_idx = item.find(f"{duration}分钟")
            if split_idx > 0:
                title_part = item[:split_idx].strip()
        title_part = re.sub(r"\s*\|\s*.*$", "", title_part).strip()

        metrics_tail = ""
        if relative_time:
            tail_match = re.search(re.escape(relative_time) + r"(.*)$", item)
            metrics_tail = tail_match.group(1).strip() if tail_match else ""
        metrics = split_play_comment_numbers(metrics_tail)

        episodes.append(
            {
                "podcast_title": title,
                "podcast_url": url,
                "episode_title": title_part,
                "published_relative": relative_time,
                "published_at_est": published_at,
                "duration_minutes": duration,
                "play_count": metrics["plays"],
                "comment_count": metrics["comments"],
                "like_count": None,
                "completion_rate": None,
                "data_source": "web_fallback",
            }
        )

    return {
        "podcast_title": title,
        "podcast_url": url,
        "subscribers": subscribers,
        "episodes": episodes,
    }


def build_data(
    urls: List[str],
    xyz_base_url: Optional[str],
    xyz_token: Optional[str],
    device_id: Optional[str],
    app_version: str,
    app_build: str,
    limit: int,
) -> pd.DataFrame:
    now = datetime.now()
    all_rows: List[Dict] = []
    for url in urls:
        parsed = None
        if xyz_token and device_id:
            try:
                parsed = fetch_podcast_by_api(
                    xyz_token, device_id, url, limit=limit, app_version=app_version, app_build=app_build
                )
                print(f"[api] OK {parsed['podcast_title']} ({len(parsed['episodes'])} eps)")
            except Exception as exc:
                print(f"[api] FAIL {url}: {exc}")

        if parsed is None and xyz_token and xyz_base_url:
            try:
                parsed = fetch_podcast_by_xyz(xyz_base_url, xyz_token, url, limit=limit)
                print(f"[xyz] OK {parsed['podcast_title']} ({len(parsed['episodes'])} eps)")
            except Exception as exc:
                print(f"[xyz] FAIL {url}: {exc}")

        if parsed is None:
            parsed = fetch_podcast_from_next_data(url, limit=limit)
            if parsed is not None:
                print(f"[web] {parsed['podcast_title']} ({len(parsed['episodes'])} eps, Next.js)")
            else:
                text = fetch_page_text(url)
                parsed = parse_podcast(text, url, now)
                print(f"[web] {parsed['podcast_title']} ({len(parsed['episodes'])} eps, text)")

        all_rows.extend(parsed["episodes"])
        for row in all_rows[-len(parsed["episodes"]) :]:
            row["subscribers"] = parsed["subscribers"]

    expected_columns = [
        "podcast_title",
        "podcast_url",
        "episode_title",
        "published_relative",
        "published_at_est",
        "duration_minutes",
        "play_count",
        "comment_count",
        "like_count",
        "completion_rate",
        "subscribers",
        "data_source",
    ]
    df = pd.DataFrame(all_rows)
    if df.empty:
        return pd.DataFrame(columns=expected_columns)

    df["published_at_est"] = pd.to_datetime(df["published_at_est"], errors="coerce", utc=True).dt.tz_convert(None)
    df["days_since_publish"] = (pd.Timestamp.now() - df["published_at_est"]).dt.days
    df["duration_minutes"] = pd.to_numeric(df["duration_minutes"], errors="coerce")
    df["play_count"] = pd.to_numeric(df["play_count"], errors="coerce")
    df["comment_count"] = pd.to_numeric(df["comment_count"], errors="coerce")
    df["like_count"] = pd.to_numeric(df["like_count"], errors="coerce")
    df["subscribers"] = pd.to_numeric(df["subscribers"], errors="coerce")
    return df


def summary_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows = []
    for podcast, part in df.groupby("podcast_title"):
        part = part.sort_values("published_at_est", ascending=False)
        freq_days = None
        if len(part) > 1:
            deltas = (
                part["published_at_est"].sort_values(ascending=False).diff().dropna().abs()
            )
            if not deltas.empty:
                freq_days = round(deltas.dt.days.mean(), 2)

        rows.append(
            {
                "podcast_title": podcast,
                "subscriber_count": part["subscribers"].dropna().max(),
                "episodes_captured": len(part),
                "avg_update_interval_days": freq_days,
                "avg_duration_minutes": round(part["duration_minutes"].mean(), 2)
                if not part["duration_minutes"].dropna().empty
                else None,
                "avg_play_count": round(part["play_count"].mean(), 2)
                if not part["play_count"].dropna().empty
                else None,
                "avg_comment_count": round(part["comment_count"].mean(), 2)
                if not part["comment_count"].dropna().empty
                else None,
            }
        )
    return pd.DataFrame(rows)


def export_excel(df: pd.DataFrame, summary: pd.DataFrame, output_path: Path) -> None:
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="summary")
        df.to_excel(writer, index=False, sheet_name="episodes_all")
        for podcast, part in df.groupby("podcast_title"):
            sheet = re.sub(r"[\[\]\*\?\/\\:]", "_", podcast)[:31] or "podcast"
            part.to_excel(writer, index=False, sheet_name=sheet)


def export_html(df: pd.DataFrame, summary: pd.DataFrame, output_path: Path) -> None:
    if df.empty:
        output_path.write_text("<h1>No data</h1>", encoding="utf-8")
        return

    charts = []
    if not summary.empty:
        fig1 = px.bar(
            summary.sort_values("subscriber_count", ascending=False),
            x="podcast_title",
            y="subscriber_count",
            title="订阅量（公开页可见值）",
        )
        charts.append(fig1.to_html(full_html=False, include_plotlyjs="cdn"))

    valid_time_df = df.dropna(subset=["published_at_est"]).copy()
    if not valid_time_df.empty:
        valid_time_df["publish_date"] = valid_time_df["published_at_est"].dt.date
        fig2 = px.scatter(
            valid_time_df,
            x="publish_date",
            y="podcast_title",
            size="duration_minutes",
            color="podcast_title",
            title="发布时间轴（点大小代表时长）",
        )
        charts.append(fig2.to_html(full_html=False, include_plotlyjs=False))

    fig3 = px.box(
        df.dropna(subset=["duration_minutes"]),
        x="podcast_title",
        y="duration_minutes",
        title="单集时长分布（分钟）",
    )
    charts.append(fig3.to_html(full_html=False, include_plotlyjs=False))

    fig4 = px.bar(
        df.dropna(subset=["comment_count"])
        .sort_values("comment_count", ascending=False)
        .head(30),
        x="episode_title",
        y="comment_count",
        color="podcast_title",
        title="评论数 Top 30（抓取样本）",
    )
    fig4.update_layout(xaxis_tickangle=-45)
    charts.append(fig4.to_html(full_html=False, include_plotlyjs=False))

    summary_html = summary.to_html(index=False)
    episodes_preview_html = df.head(100).to_html(index=False)

    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>小宇宙播客看板</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; }}
    h1, h2 {{ margin-top: 28px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; }}
    th {{ background: #f6f6f6; }}
    .note {{ color: #666; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>小宇宙播客数据看板</h1>
  <p class="note">说明：粉丝/订阅/播放/完播率等平台私有指标可能缺失，当前以公开页面可抓字段为主。</p>

  <h2>节目汇总</h2>
  {summary_html}

  <h2>图表</h2>
  {''.join(charts)}

  <h2>集数明细（前 100 行）</h2>
  {episodes_preview_html}
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def upsert_history_sqlite(df: pd.DataFrame, db_path: Path, snapshot_at: datetime) -> int:
    """将本次抓取明细追加到 SQLite 历史表，按唯一键去重。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episode_history (
                snapshot_at TEXT NOT NULL,
                podcast_title TEXT,
                podcast_url TEXT,
                episode_title TEXT,
                published_relative TEXT,
                published_at_est TEXT,
                duration_minutes REAL,
                play_count REAL,
                comment_count REAL,
                like_count REAL,
                completion_rate REAL,
                subscribers REAL,
                data_source TEXT,
                days_since_publish REAL,
                UNIQUE(snapshot_at, podcast_title, episode_title, published_at_est)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episode_history_pub ON episode_history(published_at_est)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episode_history_podcast ON episode_history(podcast_title)"
        )

        if df.empty:
            conn.commit()
            return 0

        rows_df = df.copy()
        rows_df["snapshot_at"] = snapshot_at.strftime("%Y-%m-%d %H:%M:%S")
        rows_df["published_at_est"] = pd.to_datetime(
            rows_df["published_at_est"], errors="coerce"
        ).dt.strftime("%Y-%m-%d %H:%M:%S")

        columns = [
            "snapshot_at",
            "podcast_title",
            "podcast_url",
            "episode_title",
            "published_relative",
            "published_at_est",
            "duration_minutes",
            "play_count",
            "comment_count",
            "like_count",
            "completion_rate",
            "subscribers",
            "data_source",
            "days_since_publish",
        ]
        rows = []
        for row in rows_df[columns].itertuples(index=False, name=None):
            normalized = tuple(None if pd.isna(v) else v for v in row)
            rows.append(normalized)

        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO episode_history (
                snapshot_at, podcast_title, podcast_url, episode_title, published_relative,
                published_at_est, duration_minutes, play_count, comment_count, like_count,
                completion_rate, subscribers, data_source, days_since_publish
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()


def persist_data_warehouse(
    df: pd.DataFrame,
    summary: pd.DataFrame,
    snapshot_at: datetime,
    data_dir: Path,
) -> Dict[str, Path]:
    """落地三层数据：snapshot（不可覆盖）+ latest（覆盖）+ SQLite 历史库。"""
    snapshots_dir = data_dir / "snapshots"
    latest_dir = data_dir / "latest"
    db_dir = data_dir / "database"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    db_dir.mkdir(parents=True, exist_ok=True)

    ts = snapshot_at.strftime("%Y%m%d_%H%M%S")
    snapshot_csv = snapshots_dir / f"{ts}.csv"
    latest_csv = latest_dir / "latest.csv"
    latest_summary_csv = latest_dir / "latest_summary.csv"
    db_path = db_dir / "podcast.db"

    df_with_snapshot = df.copy()
    df_with_snapshot["snapshot_at"] = snapshot_at.strftime("%Y-%m-%d %H:%M:%S")

    df_with_snapshot.to_csv(snapshot_csv, index=False, encoding="utf-8-sig")
    df_with_snapshot.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    summary.to_csv(latest_summary_csv, index=False, encoding="utf-8-sig")
    inserted = upsert_history_sqlite(df, db_path, snapshot_at)
    print(f"[OK] Snapshot CSV: {snapshot_csv}")
    print(f"[OK] Latest CSV:   {latest_csv}")
    print(f"[OK] Latest Summary CSV: {latest_summary_csv}")
    print(f"[OK] SQLite:      {db_path} (inserted {inserted} rows)")

    return {
        "snapshot_csv": snapshot_csv,
        "latest_csv": latest_csv,
        "latest_summary_csv": latest_summary_csv,
        "sqlite_db": db_path,
    }


def load_urls(path: Path) -> List[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取小宇宙播客（优先 xyz API）并输出 Excel + HTML。")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("podcasts.txt"),
        help="播客链接列表文件，每行一个 URL。",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("output"),
        help="输出目录。",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="数据仓库目录（snapshots/latest/database）。",
    )
    parser.add_argument("--limit", type=int, default=100, help="每个播客抓取集数上限。")
    parser.add_argument("--xyz-base-url", type=str, default=None, help="xyz 地址")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    urls = load_urls(args.input)
    if not urls:
        raise ValueError("输入链接为空，请在 podcasts.txt 里添加 URL。")

    env_data = load_env_file()
    xyz_base_url = args.xyz_base_url or os.getenv("XYZ_BASE_URL") or env_data.get("XYZ_BASE_URL") or "http://localhost:23020"
    xyz_token = os.getenv("XYZ_ACCESS_TOKEN") or env_data.get("XYZ_ACCESS_TOKEN")
    device_id = os.getenv("XYZ_DEVICE_ID") or env_data.get("XYZ_DEVICE_ID")
    app_version = os.getenv("XYZ_APP_VERSION") or env_data.get("XYZ_APP_VERSION") or "2.68"
    app_build = os.getenv("XYZ_APP_BUILDNO") or env_data.get("XYZ_APP_BUILDNO") or "1858"
    if not xyz_token:
        print("[WARN] 未配置 XYZ_ACCESS_TOKEN，将回退网页抓取（数据可能为空）。")
    elif not device_id:
        print("[WARN] 未配置 XYZ_DEVICE_ID，直连 API 可能 401；请在 .env 增加抓包里的 x-jike-device-id。")

    df = build_data(
        urls,
        xyz_base_url=xyz_base_url,
        xyz_token=xyz_token,
        device_id=device_id,
        app_version=app_version,
        app_build=app_build,
        limit=args.limit,
    )
    summary = summary_table(df)

    snapshot_at = datetime.now()
    timestamp = snapshot_at.strftime("%Y%m%d_%H%M%S")
    excel_path = args.out_dir / f"xiaoyuzhou_dashboard_{timestamp}.xlsx"
    html_path = args.out_dir / f"xiaoyuzhou_dashboard_{timestamp}.html"
    csv_path = args.out_dir / f"xiaoyuzhou_episodes_{timestamp}.csv"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    export_excel(df, summary, excel_path)
    export_html(df, summary, html_path)
    persist_data_warehouse(df, summary, snapshot_at=snapshot_at, data_dir=args.data_dir)

    print(f"[OK] CSV:   {csv_path}")
    print(f"[OK] Excel: {excel_path}")
    print(f"[OK] HTML:  {html_path}")


if __name__ == "__main__":
    main()
