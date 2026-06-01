# 小宇宙播客抓取 Kit

抓取小宇宙公开页面数据，输出多种格式，并支持 Streamlit 看板本地运行或在线分享。

**输出格式：**
- `CSV`：单集明细数据
- `Excel`：汇总 + 明细 + 分播客 sheet
- `HTML`：本地静态看板（Plotly）
- `Streamlit`：交互看板，支持筛选排序，可部署到云端分享

---

## 1) 安装依赖

```bash
cd podcast_kit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 2) 配置播客链接

编辑 `podcasts.txt`，每行一个小宇宙节目 URL。

当前已预置 3 个节目：

- `https://www.xiaoyuzhoufm.com/podcast/626b46ea9cbbf0451cf5a962`
- `https://www.xiaoyuzhoufm.com/podcast/6022a180ef5fdaddc30bb101`
- `https://www.xiaoyuzhoufm.com/podcast/5e5c52c9418a84a04625e6cc`

---

## 3) 登录获取 Token

```bash
source .venv/bin/activate
python login_consumer.py --phone "你的手机号" --send-only
python login_consumer.py --phone "你的手机号" --code "短信验证码" --device-id "上一步输出的 device-id"
```

Token 会写入 `.env` 的 `XYZ_ACCESS_TOKEN`。

> **注意：** `.env` 包含敏感信息，不要推送到 GitHub。

---

## 4) 运行抓取

```bash
python scrape_xiaoyuzhou.py --limit 200
```

输出文件保存在 `output/` 目录。

---

## 5) 启动 Streamlit 看板（本地）

```bash
source .venv/bin/activate
streamlit run dashboard.py
```

浏览器会自动打开，点击"⟳ 刷新数据"可加载最新抓取结果。

---

## 6) 部署到 Streamlit Cloud（在线分享）

每次抓取完成后，将最新数据推送到 GitHub：

```bash
git add output/
git commit -m "update data"
git push
```

Streamlit Cloud 会自动重新部署，分享链接即可让他人查看。

> 部署地址：[share.streamlit.io](https://share.streamlit.io)，用 GitHub 账号登录后选择本仓库，主文件设为 `dashboard.py`。

---

## 字段说明

| 字段 | 说明 |
|------|------|
| `subscribers` | 订阅数（公开可见） |
| `duration_minutes` | 单集时长 |
| `play_count` | 播放量 |
| `comment_count` | 评论数 |
| `like_count` | 点赞数 |
| `avg_update_interval_days` | 平均更新间隔（计算得出） |
| `completion_rate` | 完播率（需平台后台权限，当前为空） |

---

## 定时自动抓取（每周一 9 点）

```bash
crontab -e
```

添加：

```cron
0 9 * * 1 cd /Users/heshihan/Documents/podcast_kit && .venv/bin/python scrape_xiaoyuzhou.py --limit 200 >> cron.log 2>&1 && git add output/ && git commit -m "auto update" && git push
```

这样每周自动抓取并推送，Streamlit Cloud 看板同步更新。

---

## 注意事项

1. 小宇宙页面结构变更时，解析规则可能需要微调。
2. 播放/评论在个别页面可能没有明确标签，脚本会做启发式拆分，建议抽样核验。
3. 完播率等运营指标不在公开页，需要账号权限数据源。
4. `.env` 文件包含登录 Token，**不要**提交到 GitHub。
