#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日新闻聚合抓取脚本。

读取 config/feeds.yaml 定义的分类与 RSS/Atom 源，抓取后做关键词过滤、
跨天去重，最后输出：
    data/news.json              首页数据（最近 retention_days 天）
    data/archive/YYYY-MM-DD.json 当天新增条目归档
    data/archive/index.json     归档日期索引

仅依赖 PyYAML；HTTP 与 XML 解析使用 Python 标准库。

用法：
    python3 scripts/fetch_news.py
    python3 scripts/fetch_news.py --config config/feeds.yaml --data-dir data
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 PyYAML，请先执行：pip install -r scripts/requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "feeds.yaml"
DEFAULT_DATA_DIR = ROOT / "data"

# 部分站点/CDN 会拦截 "bot" 类 UA，这里使用浏览器 UA 提高兼容性
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
TRACKING_RE = re.compile(r"^(utm_|spm|from|ref|share_)")


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def find_child(element, name: str):
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def find_children(element, name: str):
    return [c for c in element if local_name(c.tag) == name]


def child_text(element, name: str) -> str:
    child = find_child(element, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def clean_text(raw: str, limit: int = 300) -> str:
    if not raw:
        return ""
    text = html.unescape(TAG_RE.sub(" ", raw))
    text = WS_RE.sub(" ", text).strip()
    return text[:limit]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if "?" in url:
        base, query = url.split("?", 1)
        kept = [p for p in query.split("&") if p and not TRACKING_RE.match(p)]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url.rstrip("/")


def parse_datetime(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def make_id(url: str, title: str) -> str:
    basis = normalize_url(url) or "title:" + title.lower()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# 网络与解析
# --------------------------------------------------------------------------
def http_get(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_feed(payload: bytes):
    """解析 RSS 2.0 / RSS 1.0(RDF) / Atom，返回条目字典列表。"""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"XML 解析失败: {exc}") from exc

    root_name = local_name(root.tag)
    nodes = []
    if root_name == "rss":
        channel = find_child(root, "channel")
        nodes = find_children(channel, "item") if channel is not None else []
    elif root_name == "RDF":
        nodes = find_children(root, "item")
    elif root_name == "feed":  # Atom
        nodes = find_children(root, "entry")
    else:
        nodes = find_children(root, "item") or find_children(root, "entry")

    entries = []
    for node in nodes:
        title = clean_text(child_text(node, "title"), 200)
        link = child_text(node, "link")
        if not link:  # Atom
            for candidate in find_children(node, "link"):
                rel = candidate.attrib.get("rel", "alternate")
                href = candidate.attrib.get("href", "")
                if rel in ("alternate", "") and href:
                    link = href
                    break
        summary = clean_text(
            child_text(node, "description")
            or child_text(node, "summary")
            or child_text(node, "content"),
            300,
        )
        published = parse_datetime(
            child_text(node, "pubDate")
            or child_text(node, "published")
            or child_text(node, "updated")
            or child_text(node, "date")
        )
        entries.append(
            {
                "title": title,
                "url": link.strip(),
                "summary": summary,
                "published": to_utc(published) if published else None,
            }
        )
    return entries


# --------------------------------------------------------------------------
# 抓取主流程
# --------------------------------------------------------------------------
def matches(text: str, keywords) -> bool:
    """keywords 为空表示不过滤；命中任意一个关键词即保留。"""
    if not keywords:
        return True
    lowered = text.lower()
    return any(str(kw).lower() in lowered for kw in keywords)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def fetch_category(category: dict, settings: dict, now: datetime, log):
    """抓取单个分类，返回 (条目列表, 成功源数, 失败源数)。"""
    timeout = int(settings.get("request_timeout", 20))
    max_per_feed = int(settings.get("max_items_per_feed", 15))
    exclude_keywords = settings.get("exclude_keywords") or []
    category_keywords = category.get("keywords") or []
    category_id = category.get("id", "misc")
    category_name = category.get("name", category_id)

    collected = []
    ok = failed = 0
    for feed in category.get("feeds") or []:
        if feed.get("disabled"):
            continue
        name = feed.get("name", "未命名来源")
        url = feed.get("url")
        if not url:
            continue
        keywords = feed["keywords"] if "keywords" in feed else category_keywords
        try:
            entries = parse_feed(http_get(url, timeout))
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
            failed += 1
            log(f"  [跳过] {name}: {exc}")
            continue

        ok += 1
        kept = 0
        for entry in entries:
            if not entry["title"] or not entry["url"]:
                continue
            if entry["published"] is None:
                entry["published"] = now
            blob = f"{entry['title']} {entry['summary']}"
            if not matches(blob, keywords):
                continue
            if exclude_keywords and matches(blob, exclude_keywords):
                continue
            collected.append(
                {
                    "id": make_id(entry["url"], entry["title"]),
                    "title": entry["title"],
                    "url": entry["url"],
                    "source": name,
                    "category": category_id,
                    "category_name": category_name,
                    "published": entry["published"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "summary": entry["summary"],
                    "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            kept += 1
            if kept >= max_per_feed:
                break
        log(f"  [成功] {name}: {kept} 条")
    return collected, ok, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 RSS/Atom 并生成站点数据")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    def log(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}", file=sys.stderr)
        return 1
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    settings = config.get("settings") or {}
    categories = config.get("categories") or []

    data_dir = Path(args.data_dir)
    news_path = data_dir / "news.json"
    archive_dir = data_dir / "archive"

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    retention_days = int(settings.get("retention_days", 3))
    max_total = int(settings.get("max_total_items", 600))

    previous_items = load_json(news_path, {}).get("items", []) or []
    known_ids = {item["id"] for item in previous_items}

    fresh_items = []
    total_ok = total_failed = 0
    for category in categories:
        log(f"[分类] {category.get('name', category.get('id'))}")
        items, ok, failed = fetch_category(category, settings, now, log)
        fresh_items.extend(items)
        total_ok += ok
        total_failed += failed

    # 本次新增（跨运行去重）
    new_items, seen = [], set()
    for item in fresh_items:
        if item["id"] in known_ids or item["id"] in seen:
            continue
        seen.add(item["id"])
        new_items.append(item)
    log(f"\n新增 {len(new_items)} 条（源: 成功 {total_ok} / 失败 {total_failed}）")

    # 合并并按保留期裁剪
    merged = {item["id"]: item for item in previous_items}
    for item in new_items:
        merged[item["id"]] = item
    # 保留策略基于「发现时间」而非发布时间：更新慢的源（周刊、博客）也能在首页停留完整周期
    cutoff = now - timedelta(days=retention_days)
    kept = [
        item
        for item in merged.values()
        if datetime.strptime(
            item.get("fetched_at") or item["published"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        >= cutoff
    ]
    kept.sort(key=lambda item: item["published"], reverse=True)
    kept = kept[:max_total]

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "retention_days": retention_days,
        "categories": [
            {
                "id": c.get("id"),
                "name": c.get("name", c.get("id")),
                "color": c.get("color", "#6366f1"),
            }
            for c in categories
        ],
        "sources": [
            {"name": f.get("name"), "category": c.get("id")}
            for c in categories
            for f in (c.get("feeds") or [])
            if not f.get("disabled")
        ],
        "stats": {
            "total": len(kept),
            "new": len(new_items),
            "feeds_ok": total_ok,
            "feeds_failed": total_failed,
        },
        "items": kept,
    }
    write_json(news_path, payload)

    # 当天归档（同一天多次运行做合并）
    archive_path = archive_dir / f"{today}.json"
    archive = load_json(archive_path, {"date": today, "items": []})
    archive_ids = {item["id"] for item in archive.get("items", [])}
    archive_items = archive.get("items", []) + [
        item for item in new_items if item["id"] not in archive_ids
    ]
    archive_items.sort(key=lambda item: item["published"], reverse=True)
    write_json(
        archive_path,
        {
            "date": today,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(archive_items),
            "items": archive_items,
        },
    )

    # 归档索引
    days = sorted(
        {p.stem for p in archive_dir.glob("*.json") if p.stem != "index"}, reverse=True
    )
    write_json(archive_dir / "index.json", {"days": days[:90]})

    log(f"已写入 {news_path.relative_to(ROOT)}（{len(kept)} 条）")
    log(f"已写入 {archive_path.relative_to(ROOT)}（{len(archive_items)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
