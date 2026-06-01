 小宇宙播客抓取 Kit（Excel + HTML）

这套脚本用于抓取小宇宙公开页面可见数据，并输出：

- `CSV`：明细数据
- `Excel`：汇总 + 明细 + 分播客 sheet
- `HTML`：本地可交互看板（Plotly）

## 1) 安装依赖

```bash
cd podcast_kit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) 配置播客链接

编辑 `podcasts.txt`，每行一个小宇宙节目 URL。

当前已预置 3 个节目：

- `https://www.xiaoyuzhoufm.com/podcast/626b46ea9cbbf0451cf5a962`
- `https://www.xiaoyuzhoufm.com/podcast/6022a180ef5fdaddc30bb101`
- `https://www.xiaoyuzhoufm.com/podcast/5e5c52c9418a84a04625e6cc`

## 3) 启动 xyz + 登录（拿 token）

终端 A（保持运行）：

```bash
cd "/Users/heshihan/Documents/Remi/xyz"
go run .
```

终端 B：

- 文档页：`http://localhost:23020/docs/`（浏览器可开）
- `http://localhost:23020/login` **不要**在浏览器打开（仅 POST API，会 404）
- xyz 自带 `curl .../sendCode` 走**主播后台**，听友号常返回 400

请用听播客 App 登录脚本：

```bash
cd "/Users/heshihan/Documents/Remi/podcast_kit"
source .venv/bin/activate
python login_consumer.py --phone "你的手机号" --send-only
python login_consumer.py --phone "你的手机号" --code "短信验证码" --device-id "上一步输出的 device-id"
```

会写入 `.env` 的 `XYZ_ACCESS_TOKEN`。

## 4) 运行抓取

```bash
python scrape_xiaoyuzhou.py --limit 200
```

输出文件在 `output/` 目录。

## 字段说明（对应你的指标）

- 可直接抓取（公开可见）：`subscribers`、`duration_minutes`、`play_count`、`comment_count`
- 可计算：`avg_update_interval_days`（更新频率）
- 预留待补：`like_count`、`completion_rate`（常需平台后台或 API）

## 定时任务（每周一早上 9 点）

```bash
crontab -e
```

加入：

```cron
0 9 * * 1 cd /Users/heshihan/Documents/Remi/podcast_kit && /usr/bin/python3 scrape_xiaoyuzhou.py >> cron.log 2>&1
```

## 注意

1. 小宇宙页面结构变更时，解析规则可能需要微调。  
2. 播放/评论在个别页面可能没有明确标签，脚本会做启发式拆分，建议在结果中抽样核验。  
3. 完播率等核心运营指标一般不在公开页，需要账号权限数据源。
