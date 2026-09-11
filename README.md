# TechPulse · 每日资讯聚合站

一个**纯静态**的资讯聚合网站：GitHub Actions 每天定时抓取 RSS / Atom 源，
按「人工智能 / 金融财经 / 计算机科学与技术」分类聚合，生成 JSON 数据，前端直接读取渲染。

- 无后端、无数据库、无 Jekyll / Ruby 依赖
- 关注方向完全由 `config/feeds.yaml` 决定，改配置即可增删分类与资讯源
- 可选 LLM 一句话中文摘要（配了 API Key 才启用，未配置自动跳过）

## 目录结构

```
.
├── index.html                 # 首页（前端渲染）
├── about.html / 404.html
├── assets/
│   ├── style.css              # 深浅双主题样式
│   └── app.js                 # 筛选、搜索、按天归档、主题切换
├── config/
│   └── feeds.yaml             # 关注方向配置：分类 + 源 + 关键词 + 保留策略
├── data/                      # 自动生成，不用手改
│   ├── news.json              # 首页数据
│   ├── summaries.json         # AI 摘要（可选）
│   └── archive/YYYY-MM-DD.json
├── scripts/
│   ├── fetch_news.py          # 抓取 / 去重 / 过滤 / 归档
│   ├── summarize.py           # 可选：LLM 一句话摘要
│   └── requirements.txt
└── .github/workflows/daily-news.yml   # 每天 08:00 / 20:00（北京时间）自动更新
```

## 本地运行

```bash
pip install -r scripts/requirements.txt
python3 scripts/fetch_news.py     # 抓取并生成 data/news.json
python3 -m http.server 8000       # 打开 http://localhost:8000
```

> 直接双击 `index.html`（`file://`）会因浏览器安全策略读不到 JSON，请用上面的本地服务器方式预览。

## 定制关注方向

编辑 `config/feeds.yaml`：

```yaml
settings:
  retention_days: 3        # 首页保留最近几天
  max_items_per_feed: 15   # 单个源每次最多取多少条

categories:
  - id: ai                 # 分类标识
    name: 人工智能          # 显示名
    color: "#6366f1"       # 主题色
    keywords: []           # 空 = 不过滤；填写后命中标题/摘要才保留
    feeds:
      - name: 量子位
        url: https://www.qbitai.com/feed
      - name: Hacker News
        url: https://hnrss.org/frontpage
        keywords: [AI, LLM, Rust, Linux, security]   # 源级关键词，覆盖分类级
      - name: 某源
        url: https://example.com/feed
        disabled: true      # 临时停用
```

改完提交推送即可，前端会自动读取新的分类、名称与配色；下一次定时任务生效。

## 自动更新原理

`.github/workflows/daily-news.yml` 每天 UTC 00:00 / 12:00（北京时间 08:00 / 20:00）触发：

1. 安装 PyYAML → 运行 `scripts/fetch_news.py`
2. 可选运行 `scripts/summarize.py`（有 API Key 时生成中文一句话摘要）
3. 若 `data/` 有变化则自动 commit & push，GitHub Pages 随之更新

也可以在仓库的 Actions 页面手动 `Run workflow` 立即触发。

修改更新时间改 workflow 里的 `cron` 表达式（注意 GitHub Actions 使用 UTC）。

## 部署

仓库 Settings → Pages → Build and deployment 选择 **Deploy from a branch**，
分支选 `master`、目录选 `/ (root)` 即可（根目录的 `.nojekyll` 会跳过 Jekyll 构建，直接托管静态文件）。

## AI 摘要配置（可选）

在仓库 Settings → Secrets and variables → Actions 中添加：

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | Secret | OpenAI，或 DeepSeek / Moonshot / 通义等兼容接口 |
| `OPENAI_BASE_URL` | Variable | 兼容接口地址，如 `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | Variable | 模型名，如 `deepseek-chat`、`gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | Secret | Claude（设置了它则优先使用） |
| `ANTHROPIC_MODEL` | Variable | 默认 `claude-sonnet-4-20250514` |

未配置任何 Key 时摘要步骤自动跳过，站点功能不受影响。
不想用摘要可在 `config/feeds.yaml` 里设 `ai.enabled: false`。

## 说明

- 抓取失败的源会被跳过并记录日志，不影响整体运行（部分源可能对地区 / 网络有限制）
- 首页保留策略按「发现时间」计算，更新慢的周刊、博客也能完整停留一个周期
- 内容版权归原作者与来源站点所有，本站仅做标题与摘要聚合，点击标题跳转原文
