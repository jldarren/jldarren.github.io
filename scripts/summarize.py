#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可选步骤：用 LLM 为新闻生成一句话中文摘要。

设计原则：
  * 未配置任何 API Key、或 config/feeds.yaml 中 ai.enabled=false 时直接跳过（退出码 0）
  * 摘要单独存 data/summaries.json，按条目 id 关联，不污染 news.json / 归档
  * 只依赖 Python 标准库

支持的服务端：
  1) OpenAI 兼容接口（OpenAI / DeepSeek / Moonshot / 通义 / 本地 Ollama 等）
     环境变量：OPENAI_API_KEY，可选 OPENAI_BASE_URL（默认 https://api.openai.com/v1）、OPENAI_MODEL
  2) Anthropic Claude
     环境变量：ANTHROPIC_API_KEY，可选 ANTHROPIC_MODEL

在 GitHub Actions 中把 Key 配成仓库 Secrets 即可，未配置则本脚本自动跳过。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 PyYAML，请先执行：pip install -r scripts/requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "feeds.yaml"
DEFAULT_DATA_DIR = ROOT / "data"

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_VERSION = "2023-06-01"

PROMPT_TEMPLATE = (
    "你是资深资讯编辑。下面是若干条科技/财经新闻，JSON 数组，字段为 id/title/source/summary。"
    "请为每一条生成一句不超过 40 个汉字的中文摘要，客观陈述核心事实，"
    "不要臆测、不要添加原文没有的信息、不要写\"本文介绍了\"这类套话。"
    "只输出一个 JSON 对象，键为条目的 id，值为摘要字符串；"
    "不要输出解释、不要 Markdown 代码块。"
)


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, flush=True)


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
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def post_json(url: str, body: dict, headers: dict, timeout: int = 120) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def call_openai_compatible(prompt: str) -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base = os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE).rstrip("/")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    payload = post_json(
        f"{base}/chat/completions",
        {
            "model": model,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        },
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    return payload["choices"][0]["message"]["content"], f"openai:{model}"


def call_anthropic(prompt: str) -> tuple[str, str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    payload = post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        },
        {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    text = "".join(
        block.get("text", "") for block in payload.get("content", []) if isinstance(block, dict)
    )
    return text, f"anthropic:{model}"


def extract_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("模型输出中未找到 JSON 对象")
    return json.loads(cleaned[start : end + 1])


def prune(summaries: dict, data_dir: Path) -> dict:
    """丢弃已不在首页数据/近期归档中的条目，避免 summaries.json 无限增长。"""
    keep = set()
    news = load_json(data_dir / "news.json", {"items": []})
    keep.update(item.get("id") for item in news.get("items") or [])
    archive_dir = data_dir / "archive"
    for path in sorted(archive_dir.glob("*.json"), reverse=True)[:30]:
        if path.name == "index.json":
            continue
        archive = load_json(path, {"items": []})
        keep.update(item.get("id") for item in archive.get("items") or [])
    return {key: value for key, value in summaries.items() if key in keep}


def main() -> int:
    parser = argparse.ArgumentParser(description="为新闻生成一句话中文摘要（可选）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--force", action="store_true", help="忽略 ai.enabled 配置强制运行")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    quiet = args.quiet

    config = yaml.safe_load(
        Path(args.config).expanduser().resolve().read_text(encoding="utf-8")
    ) or {}
    ai_config = config.get("ai") or {}
    if not ai_config.get("enabled", True) and not args.force:
        log("ai.enabled=false，跳过摘要步骤", quiet)
        return 0

    use_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    use_openai = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if not (use_anthropic or use_openai):
        log("未配置 ANTHROPIC_API_KEY / OPENAI_API_KEY，跳过摘要步骤", quiet)
        return 0

    data_dir = Path(args.data_dir)
    news = load_json(data_dir / "news.json", {"items": []})
    items = news.get("items") or []
    if not items:
        log("news.json 为空，跳过摘要步骤", quiet)
        return 0

    summaries_path = data_dir / "summaries.json"
    store = load_json(summaries_path, {"summaries": {}})
    summaries = store.get("summaries") or {}

    max_age_hours = int(ai_config.get("max_age_hours", 48))
    max_items = int(ai_config.get("max_items_per_run", 40))
    batch_size = max(1, int(ai_config.get("batch_size", 20)))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    candidates = []
    for item in items:
        if item.get("id") in summaries:
            continue
        try:
            published = datetime.strptime(
                item["published"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if published < cutoff:
            continue
        candidates.append(item)
    candidates = candidates[:max_items]

    if not candidates:
        log("没有需要摘要的新条目", quiet)
        return 0

    provider = "anthropic" if use_anthropic else "openai"
    written = 0
    model_tag = ""
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        payload = [
            {
                "id": item["id"],
                "title": item["title"],
                "source": item.get("source", ""),
                "summary": (item.get("summary") or "")[:200],
            }
            for item in batch
        ]
        prompt = f"{PROMPT_TEMPLATE}\n\n新闻列表：\n{json.dumps(payload, ensure_ascii=False)}"
        try:
            raw, model_tag = (
                call_anthropic(prompt) if use_anthropic else call_openai_compatible(prompt)
            )
            result = extract_json_object(raw)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            log(f"摘要请求失败，本批跳过：{exc}", quiet)
            continue

        for index, item in enumerate(batch):
            value = result.get(item["id"])
            if value is None:
                value = result.get(str(index)) or result.get(str(index + 1))
            if isinstance(value, str) and value.strip():
                summaries[item["id"]] = value.strip()
                written += 1
        log(f"已处理 {min(start + batch_size, len(candidates))}/{len(candidates)} 条", quiet)

    summaries = prune(summaries, data_dir)
    write_json(
        summaries_path,
        {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": provider,
            "model": model_tag,
            "count": len(summaries),
            "summaries": summaries,
        },
    )
    log(f"本次新增摘要 {written} 条，累计 {len(summaries)} 条 -> {summaries_path.name}", quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
