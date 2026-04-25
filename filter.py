#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mytvlist 自动聚合脚本

功能：
1. 从多个公开 IPTV M3U/TXT 源抓取频道；
2. 逐行解析 M3U，避免把 User-Agent 中的逗号误识别为频道名；
3. 自动分类中央台、卫视、港澳台、地方频道；
4. 生成 TXT、M3U、IPv4、IPv6、统计文件和 Docker Web 首页；
5. 单个上游失败不会中断整体更新；
6. 支持 OUTPUT_DIR、GH_PROXY、CHECK_STREAMS 环境变量。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests


REPO_OWNER = "ttbly"
REPO_NAME = "mytvlist"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main"

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ".")).resolve()
GH_PROXY = os.getenv("GH_PROXY", "").strip()
CHECK_STREAMS = os.getenv("CHECK_STREAMS", "0").strip() == "1"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
STREAM_CHECK_TIMEOUT = int(os.getenv("STREAM_CHECK_TIMEOUT", "8"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

URL_RE = re.compile(r"^(https?|rtmp|rtsp|udp|rtp)://", re.IGNORECASE)

BAD_KEYWORDS = (
    "成人",
    "情色",
    "福利",
    "午夜",
    "限制级",
    "裸",
    "porn",
    "xxx",
    "sex",
    "18+",
)

KEEP_KEYWORDS = (
    "cctv",
    "央视",
    "中央",
    "cgtn",
    "卫视",
    "凤凰",
    "翡翠",
    "无线",
    "明珠",
    "tvb",
    "viutv",
    "now",
    "港台",
    "香港",
    "澳门",
    "澳视",
    "台湾",
    "台视",
    "中视",
    "华视",
    "民视",
    "公视",
    "东森",
    "三立",
    "中天",
    "年代",
    "非凡",
    "湖南",
    "浙江",
    "江苏",
    "东方",
    "上海",
    "北京",
    "广东",
    "深圳",
    "山东",
    "四川",
    "重庆",
    "天津",
    "安徽",
    "河南",
    "河北",
    "湖北",
    "江西",
    "辽宁",
    "吉林",
    "黑龙江",
    "广西",
    "贵州",
    "云南",
    "海南",
    "新疆",
    "西藏",
    "内蒙古",
    "宁夏",
    "甘肃",
    "青海",
    "山西",
    "陕西",
    "福建",
    "厦门",
    "大湾区",
)

GROUP_RULES = [
    ("中央台", ("cctv", "央视", "中央", "cgtn")),
    ("卫视", ("卫视", "湖南", "浙江", "江苏", "东方卫视", "北京卫视", "广东卫视")),
    ("港澳频道", ("香港", "澳门", "凤凰", "翡翠", "无线", "明珠", "tvb", "viutv", "now", "澳视", "港台")),
    ("台湾频道", ("台湾", "台视", "中视", "华视", "民视", "公视", "东森", "三立", "中天", "年代", "非凡")),
    (
        "地方频道",
        (
            "北京",
            "上海",
            "天津",
            "重庆",
            "河北",
            "河南",
            "山东",
            "山西",
            "陕西",
            "安徽",
            "湖北",
            "湖南",
            "江西",
            "江苏",
            "浙江",
            "福建",
            "广东",
            "广西",
            "四川",
            "贵州",
            "云南",
            "海南",
            "辽宁",
            "吉林",
            "黑龙江",
            "内蒙古",
            "宁夏",
            "甘肃",
            "青海",
            "新疆",
            "西藏",
            "深圳",
            "厦门",
        ),
    ),
]

SOURCES = [
    {
        "name": "iptv-org-cn",
        "url": "https://iptv-org.github.io/iptv/countries/cn.m3u",
        "format": "m3u",
        "trusted": True,
    },
    {
        "name": "iptv-org-hk",
        "url": "https://iptv-org.github.io/iptv/countries/hk.m3u",
        "format": "m3u",
        "trusted": True,
    },
    {
        "name": "iptv-org-mo",
        "url": "https://iptv-org.github.io/iptv/countries/mo.m3u",
        "format": "m3u",
        "trusted": True,
    },
    {
        "name": "iptv-org-tw",
        "url": "https://iptv-org.github.io/iptv/countries/tw.m3u",
        "format": "m3u",
        "trusted": True,
    },
    {
        "name": "fanmingming-ipv6",
        "url": "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
        "format": "m3u",
        "trusted": True,
    },
    {
        "name": "YanG-1989-Gather",
        "url": "https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u",
        "format": "m3u",
        "trusted": False,
        "allow_proxy": True,
    },
    {
        "name": "hujingguang-ChinaIPTV",
        "url": "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8",
        "format": "m3u",
        "trusted": False,
        "allow_proxy": True,
    },
    {
        "name": "frankwuzp-iptv-cn",
        "url": "https://raw.githubusercontent.com/frankwuzp/iptv-cn/main/iptv.m3u",
        "format": "m3u",
        "trusted": False,
        "allow_proxy": True,
    },
    {
        "name": "Guovin-iptv-api-result-m3u",
        "url": "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.m3u",
        "format": "m3u",
        "trusted": False,
        "allow_proxy": True,
    },
    {
        "name": "Guovin-iptv-api-result-txt",
        "url": "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.txt",
        "format": "txt",
        "trusted": False,
        "allow_proxy": True,
    },
]


@dataclass
class Channel:
    name: str
    url: str
    group: str
    source: str
    ipv6: bool = False
    ok: bool | None = None


def log(message: str) -> None:
    print(message, flush=True)


def maybe_proxy(url: str, allow_proxy: bool = False) -> str:
    if not GH_PROXY or not allow_proxy:
        return url
    return GH_PROXY.rstrip("/") + "/" + url


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def comma_after_extinf(line: str) -> int:
    """返回 EXTINF 行中不在引号内的最后一个逗号位置。"""
    in_quote = False
    last = -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            last = i
    return last


def extinf_name(line: str) -> str:
    idx = comma_after_extinf(line)
    if idx >= 0:
        return clean_name(line[idx + 1 :])
    return ""


def extinf_group(line: str) -> str:
    match = re.search(r'group-title="([^"]+)"', line, re.IGNORECASE)
    if match:
        return clean_name(match.group(1))
    return ""


def clean_name(name: str) -> str:
    name = html.unescape(name or "")
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\[(?:Geo-blocked|Not 24/7|Offline|Timeout)\]", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def is_bad_name(name: str) -> bool:
    lower = name.lower()
    return any(k.lower() in lower for k in BAD_KEYWORDS)


def is_useful_channel(name: str, trusted_source: bool = False) -> bool:
    if not name or is_bad_name(name):
        return False
    if trusted_source:
        return True
    lower = name.lower()
    return any(k.lower() in lower for k in KEEP_KEYWORDS)


def is_ipv6_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return ":" in host


def get_group(name: str, fallback: str = "") -> str:
    lower = name.lower()
    for group, keys in GROUP_RULES:
        if any(k.lower() in lower for k in keys):
            return group
    if fallback:
        return fallback
    return "地方及其他"


def parse_m3u(text: str, source_name: str, trusted_source: bool = False) -> list[Channel]:
    channels: list[Channel] = []
    pending_name = ""
    pending_group = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#EXTINF"):
            pending_name = extinf_name(line)
            pending_group = extinf_group(line)
            continue

        # 这些行是播放参数，不是 URL；为了兼容多数播放器，这里不写入输出。
        if line.startswith("#"):
            continue

        if pending_name and URL_RE.match(line):
            name = clean_name(pending_name)
            url = line.strip()

            if is_useful_channel(name, trusted_source=trusted_source):
                group = get_group(name, pending_group)
                channels.append(
                    Channel(
                        name=name,
                        url=url,
                        group=group,
                        source=source_name,
                        ipv6=is_ipv6_url(url),
                    )
                )

            pending_name = ""
            pending_group = ""

    return channels


def parse_txt(text: str, source_name: str, trusted_source: bool = False) -> list[Channel]:
    channels: list[Channel] = []
    current_group = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.endswith(",#genre#"):
            current_group = clean_name(line.split(",", 1)[0])
            continue

        if "," not in line:
            continue

        name, url = line.split(",", 1)
        name = clean_name(name)
        url = url.strip()

        if not URL_RE.match(url):
            continue
        if not is_useful_channel(name, trusted_source=trusted_source):
            continue

        channels.append(
            Channel(
                name=name,
                url=url,
                group=get_group(name, current_group),
                source=source_name,
                ipv6=is_ipv6_url(url),
            )
        )

    return channels


def check_url(url: str) -> bool:
    try:
        # 轻量检测：只确认服务器能返回响应，不承诺一定可播放。
        response = requests.get(url, headers=HEADERS, timeout=STREAM_CHECK_TIMEOUT, stream=True)
        return response.status_code < 400
    except Exception:
        return False


def dedupe_channels(channels: Iterable[Channel]) -> list[Channel]:
    seen_url: set[str] = set()
    result: list[Channel] = []

    for channel in channels:
        key = channel.url.strip()
        if not key or key in seen_url:
            continue
        seen_url.add(key)
        result.append(channel)

    return result


def safe_m3u_value(value: str) -> str:
    return value.replace('"', "'").strip()


def write_txt(path: Path, channels: list[Channel]) -> None:
    grouped: dict[str, list[Channel]] = defaultdict(list)
    for channel in channels:
        grouped[channel.group].append(channel)

    with path.open("w", encoding="utf-8", newline="\n") as f:
        for group in sorted(grouped.keys()):
            f.write(f"{group},#genre#\n")
            for channel in grouped[group]:
                f.write(f"{channel.name},{channel.url}\n")
            f.write("\n")


def write_m3u(path: Path, channels: list[Channel]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for channel in channels:
            name = safe_m3u_value(channel.name)
            group = safe_m3u_value(channel.group)
            f.write(f'#EXTINF:-1 tvg-name="{name}" group-title="{group}",{name}\n')
            f.write(f"{channel.url}\n")


def write_stats(path: Path, channels: list[Channel], source_status: list[dict]) -> dict:
    groups = defaultdict(int)
    sources = defaultdict(int)

    for channel in channels:
        groups[channel.group] += 1
        sources[channel.source] += 1

    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": f"{REPO_OWNER}/{REPO_NAME}",
        "total": len(channels),
        "ipv4_or_non_ipv6": sum(1 for c in channels if not c.ipv6),
        "ipv6": sum(1 for c in channels if c.ipv6),
        "check_streams": CHECK_STREAMS,
        "groups": dict(sorted(groups.items())),
        "sources": dict(sorted(sources.items())),
        "source_status": source_status,
    }

    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stats


def write_status(path: Path, stats: dict) -> None:
    source_lines = []
    for item in stats["source_status"]:
        status = "✅" if item.get("ok") else "⚠️"
        count = item.get("count", 0)
        name = item.get("name", "")
        error = item.get("error", "")
        if error:
            source_lines.append(f"| {status} | `{name}` | {count} | `{error}` |")
        else:
            source_lines.append(f"| {status} | `{name}` | {count} |  |")

    content = f"""# IPTV 更新状态

生成时间：`{stats["generated_at"]}`

仓库：`{stats["repo"]}`

## 统计

| 项目 | 数量 |
|---|---:|
| 全部频道源 | {stats["total"]} |
| IPv4/非 IPv6 | {stats["ipv4_or_non_ipv6"]} |
| IPv6 | {stats["ipv6"]} |

## 订阅地址

| 文件 | 说明 |
|---|---|
| [`cn_tw.m3u`]({RAW_BASE}/cn_tw.m3u) | 全量 M3U |
| [`cn_tw.txt`]({RAW_BASE}/cn_tw.txt) | 全量 TXT |
| [`tv_all.txt`]({RAW_BASE}/tv_all.txt) | 全量 TXT，兼容旧文件名 |
| [`cn_tw_v4.m3u`]({RAW_BASE}/cn_tw_v4.m3u) | IPv4/非 IPv6 M3U |
| [`tv_v4.txt`]({RAW_BASE}/tv_v4.txt) | IPv4/非 IPv6 TXT |
| [`cn_tw_v6.m3u`]({RAW_BASE}/cn_tw_v6.m3u) | IPv6 M3U |
| [`tv_v6.txt`]({RAW_BASE}/tv_v6.txt) | IPv6 TXT |

## 上游源状态

| 状态 | 来源 | 解析数量 | 错误 |
|---|---|---:|---|
{chr(10).join(source_lines)}

## 分组统计

| 分组 | 数量 |
|---|---:|
"""
    for group, count in stats["groups"].items():
        content += f"| {group} | {count} |\n"

    path.write_text(content, encoding="utf-8")


def write_index(path: Path, stats: dict) -> None:
    files = [
        ("cn_tw.m3u", "全量 M3U，普通播放器优先使用"),
        ("cn_tw.txt", "全量 TXT，TVBox/DIYP 优先使用"),
        ("tv_all.txt", "全量 TXT，兼容旧文件名"),
        ("cn_tw_v4.m3u", "IPv4/非 IPv6 M3U"),
        ("tv_v4.txt", "IPv4/非 IPv6 TXT"),
        ("cn_tw_v6.m3u", "IPv6 M3U"),
        ("tv_v6.txt", "IPv6 TXT"),
        ("status.md", "更新状态"),
        ("stats.json", "统计信息"),
    ]

    links = "\n".join(
        f'<li><a href="./{html.escape(filename)}">{html.escape(filename)}</a> - {html.escape(desc)}</li>'
        for filename, desc in files
    )

    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mytvlist</title>
  <style>
    body {{
      max-width: 860px;
      margin: 40px auto;
      padding: 0 20px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.7;
    }}
    code {{
      background: #f5f5f5;
      padding: 2px 6px;
      border-radius: 6px;
    }}
    li {{ margin: 8px 0; }}
  </style>
</head>
<body>
  <h1>mytvlist</h1>
  <p>自动聚合 IPTV 订阅文件。</p>
  <p>生成时间：<code>{html.escape(stats["generated_at"])}</code></p>
  <p>频道源数量：<code>{stats["total"]}</code>；IPv4/非 IPv6：<code>{stats["ipv4_or_non_ipv6"]}</code>；IPv6：<code>{stats["ipv6"]}</code></p>
  <h2>文件</h2>
  <ul>
    {links}
  </ul>
  <h2>建议</h2>
  <p>普通 IPTV 播放器优先使用 <code>cn_tw.m3u</code>，TVBox/DIYP 优先使用 <code>cn_tw.txt</code>。</p>
  <p>本项目不托管、不缓存、不转发任何视频内容，仅整理公开网络来源中的文本链接。</p>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


def load_channels() -> tuple[list[Channel], list[dict]]:
    all_channels: list[Channel] = []
    source_status: list[dict] = []

    for source in SOURCES:
        name = source["name"]
        url = maybe_proxy(source["url"], source.get("allow_proxy", False))
        fmt = source.get("format", "m3u")
        trusted = bool(source.get("trusted", False))

        try:
            log(f"Fetching {name}: {url}")
            text = fetch_text(url)

            if fmt == "txt":
                channels = parse_txt(text, name, trusted_source=trusted)
            else:
                channels = parse_m3u(text, name, trusted_source=trusted)

            all_channels.extend(channels)
            source_status.append({"name": name, "ok": True, "count": len(channels), "error": ""})
            log(f"  OK: {len(channels)} channels")
        except Exception as exc:
            source_status.append({"name": name, "ok": False, "count": 0, "error": str(exc)[:300]})
            log(f"  WARN: {name} failed: {exc}")

    return dedupe_channels(all_channels), source_status


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    channels, source_status = load_channels()

    if CHECK_STREAMS:
        log("Checking stream URLs. This may take a while.")
        checked: list[Channel] = []
        for channel in channels:
            channel.ok = check_url(channel.url)
            if channel.ok:
                checked.append(channel)
        channels = checked

    channels = sorted(channels, key=lambda c: (c.group, c.name.lower(), c.url))

    v4_channels = [c for c in channels if not c.ipv6]
    v6_channels = [c for c in channels if c.ipv6]

    write_txt(OUTPUT_DIR / "cn_tw.txt", channels)
    write_txt(OUTPUT_DIR / "tv_all.txt", channels)
    write_txt(OUTPUT_DIR / "tv_v4.txt", v4_channels)
    write_txt(OUTPUT_DIR / "tv_v6.txt", v6_channels)

    write_m3u(OUTPUT_DIR / "cn_tw.m3u", channels)
    write_m3u(OUTPUT_DIR / "cn_tw_v4.m3u", v4_channels)
    write_m3u(OUTPUT_DIR / "cn_tw_v6.m3u", v6_channels)

    stats = write_stats(OUTPUT_DIR / "stats.json", channels, source_status)
    write_status(OUTPUT_DIR / "status.md", stats)
    write_index(OUTPUT_DIR / "index.html", stats)

    log(f"Generated {len(channels)} channels in {OUTPUT_DIR}")
    log(f"IPv4/non-IPv6: {len(v4_channels)}, IPv6: {len(v6_channels)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
