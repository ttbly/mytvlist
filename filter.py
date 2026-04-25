#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mytvlist 分类增强版

重点：
1. 源可以多，但输出分类必须整洁；
2. 按固定顺序输出：中央台 -> 卫视 -> 港澳频道 -> 台湾频道 -> 各省/直辖市/自治区地方频道 -> 地方及其他；
3. 不再因为分类过严导致大量节目丢失；
4. M3U 逐行解析，避免 User-Agent 中的逗号污染频道名；
5. 生成全量、IPv4/非 IPv6、IPv6 三套文件。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

REPO_OWNER = os.getenv("REPO_OWNER", "ttbly")
REPO_NAME = os.getenv("REPO_NAME", "mytvlist")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main"

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ".")).resolve()
GH_PROXY = os.getenv("GH_PROXY", "").strip()
CHECK_STREAMS = os.getenv("CHECK_STREAMS", "0").strip() == "1"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
STREAM_CHECK_TIMEOUT = int(os.getenv("STREAM_CHECK_TIMEOUT", "8"))
MAX_SAME_CHANNEL_URLS = int(os.getenv("MAX_SAME_CHANNEL_URLS", "8"))

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
    "成人", "情色", "福利", "午夜", "限制级", "裸", "porn", "xxx", "sex", "18+",
)

# 行政区顺序。输出时按这个顺序固定排列，避免分类乱跳。
PROVINCE_ORDER = [
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "内蒙古",
    "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南",
    "广东", "广西", "海南",
    "四川", "贵州", "云南", "西藏",
    "陕西", "甘肃", "青海", "宁夏", "新疆",
]

PROVINCE_ALIASES = {
    "北京": ("北京", "BTV", "卡酷"),
    "天津": ("天津", "TJTV"),
    "上海": ("上海", "东方", "第一财经", "哈哈炫动", "纪实人文", "五星体育"),
    "重庆": ("重庆", "CQTV"),
    "河北": ("河北", "HEBTV"),
    "山西": ("山西", "SXRTV"),
    "内蒙古": ("内蒙古", "內蒙古", "蒙语", "蒙古语"),
    "辽宁": ("辽宁", "遼寧", "沈阳", "大连"),
    "吉林": ("吉林", "延边", "延邊"),
    "黑龙江": ("黑龙江", "黑龍江", "哈尔滨"),
    "江苏": ("江苏", "江蘇", "南京", "优漫", "靓妆"),
    "浙江": ("浙江", "ZJTV", "钱江", "钱江都市"),
    "安徽": ("安徽", "AHTV"),
    "福建": ("福建", "东南", "東南", "厦门", "廈門", "海峡", "海峽"),
    "江西": ("江西", "JXTV"),
    "山东": ("山东", "山東", "齐鲁", "齊魯"),
    "河南": ("河南", "HNTV", "大象"),
    "湖北": ("湖北", "武汉", "武漢"),
    "湖南": ("湖南", "芒果", "金鹰", "金鷹", "茶频道", "快乐垂钓"),
    "广东": ("广东", "廣東", "广州", "廣州", "深圳", "珠江", "大湾区", "嘉佳"),
    "广西": ("广西", "廣西"),
    "海南": ("海南", "三沙"),
    "四川": ("四川", "成都", "峨眉"),
    "贵州": ("贵州", "貴州"),
    "云南": ("云南", "雲南"),
    "西藏": ("西藏", "藏语", "藏語"),
    "陕西": ("陕西", "陝西", "西安", "农林", "農林"),
    "甘肃": ("甘肃", "甘肅"),
    "青海": ("青海", "安多"),
    "宁夏": ("宁夏", "寧夏"),
    "新疆": ("新疆", "兵团", "兵團", "维语", "維語"),
}

CENTRAL_KEYS = (
    "CCTV", "央视", "央視", "中央电视台", "中央電視台", "中央台",
    "CGTN", "中国教育", "中國教育", "CETV", "CHC",
)

SATELLITE_KEYS = (
    "卫视", "衛視",
)

HK_KEYS = (
    "香港", "港澳", "HK", "HONG KONG", "TVB", "翡翠", "明珠", "無綫", "无线",
    "J2", "VIUTV", "VIU", "HOY", "NOW", "RTHK", "港台", "凤凰", "鳳凰",
)

MO_KEYS = (
    "澳门", "澳門", "MACAU", "MACAO", "澳视", "澳視", "莲花", "蓮花",
)

TW_KEYS = (
    "台湾", "台灣", "臺灣", "TAIWAN", "TW",
    "台视", "台視", "中视", "中視", "华视", "華視", "民视", "民視",
    "公视", "公視", "三立", "东森", "東森", "中天", "年代", "非凡",
    "TVBS", "纬来", "緯來", "八大", "壹新闻", "壹新聞", "寰宇", "镜新闻", "鏡新聞",
    "大爱", "大愛", "原住民族",
)

OTHER_KEEP_KEYS = (
    "新闻", "新聞", "综合", "綜合", "综艺", "綜藝", "体育", "體育",
    "少儿", "少兒", "卡通", "动漫", "動畫", "影视", "影視", "电影", "電影",
    "剧场", "劇場", "电视剧", "電視劇", "财经", "財經", "纪录", "紀錄",
    "纪实", "紀實", "生活", "都市", "公共", "科教", "农业", "農業",
    "戏曲", "戲曲", "音乐", "音樂", "4K", "8K", "高清",
)

GROUP_ORDER = {
    "中央台": 0,
    "卫视": 1,
    "港澳频道": 2,
    "台湾频道": 3,
}
for i, province in enumerate(PROVINCE_ORDER, start=10):
    GROUP_ORDER[f"{province}频道"] = i
GROUP_ORDER["地方及其他"] = 99

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


def norm(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\ufeff", "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def clean_name(name: str) -> str:
    name = norm(name)

    # 去掉上游经常附加的状态标记，但不删除频道主体。
    name = re.sub(r"\[(?:Geo-blocked|Not 24/7|Offline|Timeout|Geo|RAW|HD|FHD|4K)\]", "", name, flags=re.I)
    name = re.sub(r"\((?:IPv6|IPV6|ipv6|备用\d*|线路\d*|高清|超清|蓝光)\)", "", name)
    name = re.sub(r"\s+", " ", name).strip()

    # 统一常见命名
    name = name.replace("ＣＣＴＶ", "CCTV")
    name = name.replace("CCTV-", "CCTV")
    name = re.sub(r"^CCTV\s+(\d+)", r"CCTV\1", name, flags=re.I)

    return name


def channel_key(name: str) -> str:
    key = clean_name(name).upper()
    key = re.sub(r"\s+", "", key)
    key = key.replace("高清", "").replace("HD", "")
    key = key.replace("频道", "")
    key = key.replace("臺", "台")
    key = key.replace("衛視", "卫视")
    return key


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    up = text.upper()
    return any(k.upper() in up for k in keywords)


def is_bad_name(name: str) -> bool:
    return contains_any(name, BAD_KEYWORDS)


def classify_channel(name: str, fallback_group: str = "", source: str = "") -> str:
    text = f"{name} {fallback_group} {source}"
    text_up = text.upper()

    # 中央台优先，避免 CCTV 被分到地方及其他。
    if contains_any(text_up, CENTRAL_KEYS):
        return "中央台"

    # 港澳、台湾优先于省级地方台。
    if contains_any(text_up, HK_KEYS) or contains_any(text_up, MO_KEYS):
        return "港澳频道"

    if contains_any(text_up, TW_KEYS):
        return "台湾频道"

    # 只有明确叫卫视，才放卫视；避免“湖南娱乐”等地方频道误入卫视。
    if contains_any(text_up, SATELLITE_KEYS):
        return "卫视"

    # 省级/地方频道按省份拆开，而不是统一堆进“地方频道”。
    for province in PROVINCE_ORDER:
        aliases = PROVINCE_ALIASES.get(province, (province,))
        if contains_any(text_up, aliases):
            return f"{province}频道"

    # 如果上游 group-title 已经是可识别省级分组，也尽量保留到对应省份。
    fallback = norm(fallback_group)
    for province in PROVINCE_ORDER:
        if province in fallback:
            return f"{province}频道"

    return "地方及其他"


def should_keep_channel(name: str, fallback_group: str = "", trusted_source: bool = False) -> bool:
    if not name or is_bad_name(name):
        return False

    # iptv-org / fanmingming 这类结构化源不做过严过滤，避免节目丢失。
    if trusted_source:
        return True

    text = f"{name} {fallback_group}"

    return (
        contains_any(text, CENTRAL_KEYS)
        or contains_any(text, SATELLITE_KEYS)
        or contains_any(text, HK_KEYS)
        or contains_any(text, MO_KEYS)
        or contains_any(text, TW_KEYS)
        or any(contains_any(text, aliases) for aliases in PROVINCE_ALIASES.values())
        or contains_any(text, OTHER_KEEP_KEYS)
    )


def maybe_proxy(url: str, allow_proxy: bool = False) -> str:
    if not GH_PROXY or not allow_proxy:
        return url
    return GH_PROXY.rstrip("/") + "/" + url


def fetch_text(url: str) -> str:
    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            if not response.encoding or response.encoding.lower() == "iso-8859-1":
                response.encoding = response.apparent_encoding or "utf-8"

            return response.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(attempt)

    raise RuntimeError(str(last_error))


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
        return clean_name(line[idx + 1:])
    return ""


def extinf_attr(line: str, attr: str) -> str:
    match = re.search(rf'{re.escape(attr)}="([^"]*)"', line, re.IGNORECASE)
    if match:
        return norm(match.group(1))
    return ""


def is_ipv6_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except ValueError:
        return "[" in url and "]" in url

    return ":" in host


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
            pending_group = extinf_attr(line, "group-title")
            continue

        if line.startswith("#EXTGRP:") and pending_name:
            pending_group = clean_name(line.split(":", 1)[1])
            continue

        # #EXTVLCOPT、#KODIPROP 等播放参数不作为频道 URL 输出。
        if line.startswith("#"):
            continue

        if pending_name and URL_RE.match(line):
            name = clean_name(pending_name)
            url = line.strip()

            if should_keep_channel(name, pending_group, trusted_source=trusted_source):
                group = classify_channel(name, pending_group, source_name)
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

        if not should_keep_channel(name, current_group, trusted_source=trusted_source):
            continue

        channels.append(
            Channel(
                name=name,
                url=url,
                group=classify_channel(name, current_group, source_name),
                source=source_name,
                ipv6=is_ipv6_url(url),
            )
        )

    return channels


def check_url(url: str) -> bool:
    try:
        response = requests.get(url, headers=HEADERS, timeout=STREAM_CHECK_TIMEOUT, stream=True)
        return response.status_code < 400
    except Exception:
        return False


def dedupe_channels(channels: Iterable[Channel]) -> list[Channel]:
    """
    去重策略：
    1. URL 完全重复，只保留第一条；
    2. 同一个“归一化频道名”最多保留 MAX_SAME_CHANNEL_URLS 条线路；
       源多可以保留，但避免一个频道重复几十条导致列表不好用。
    """
    seen_url: set[str] = set()
    per_channel_count: defaultdict[str, int] = defaultdict(int)
    result: list[Channel] = []

    for channel in channels:
        url = channel.url.strip()
        if not url or url in seen_url:
            continue

        key = channel_key(channel.name)
        if per_channel_count[key] >= MAX_SAME_CHANNEL_URLS:
            continue

        seen_url.add(url)
        per_channel_count[key] += 1
        result.append(channel)

    return result


def sort_channels(channels: Iterable[Channel]) -> list[Channel]:
    return sorted(
        channels,
        key=lambda c: (
            GROUP_ORDER.get(c.group, 999),
            c.group,
            channel_key(c.name),
            c.source,
            c.url,
        ),
    )


def safe_m3u_value(value: str) -> str:
    return value.replace('"', "'").strip()


def write_txt(path: Path, channels: list[Channel]) -> None:
    grouped: dict[str, list[Channel]] = defaultdict(list)

    for channel in channels:
        grouped[channel.group].append(channel)

    ordered_groups = sorted(
        grouped.keys(),
        key=lambda g: (GROUP_ORDER.get(g, 999), g),
    )

    with path.open("w", encoding="utf-8", newline="\n") as f:
        for group in ordered_groups:
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


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_stats(path: Path, channels: list[Channel], source_status: list[dict]) -> dict:
    groups = Counter(c.group for c in channels)
    sources = Counter(c.source for c in channels)

    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": f"{REPO_OWNER}/{REPO_NAME}",
        "total": len(channels),
        "ipv4_or_non_ipv6": sum(1 for c in channels if not c.ipv6),
        "ipv6": sum(1 for c in channels if c.ipv6),
        "check_streams": CHECK_STREAMS,
        "max_same_channel_urls": MAX_SAME_CHANNEL_URLS,
        "group_order": sorted(groups.keys(), key=lambda g: (GROUP_ORDER.get(g, 999), g)),
        "groups": {group: groups[group] for group in sorted(groups.keys(), key=lambda g: (GROUP_ORDER.get(g, 999), g))},
        "sources": dict(sorted(sources.items())),
        "source_status": source_status,
    }

    atomic_write(path, json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    return stats


def write_status(path: Path, stats: dict) -> None:
    source_lines = []
    for item in stats["source_status"]:
        status = "✅" if item.get("ok") else "⚠️"
        count = item.get("count", 0)
        name = item.get("name", "")
        error = item.get("error", "")
        source_lines.append(f"| {status} | `{name}` | {count} | `{error}` |")

    group_lines = []
    for group, count in stats["groups"].items():
        group_lines.append(f"| {group} | {count} |")

    content = f"""# IPTV 更新状态

生成时间：`{stats["generated_at"]}`

仓库：`{stats["repo"]}`

## 统计

| 项目 | 数量 |
|---|---:|
| 全部频道源 | {stats["total"]} |
| IPv4/非 IPv6 | {stats["ipv4_or_non_ipv6"]} |
| IPv6 | {stats["ipv6"]} |
| 同一频道最多保留线路数 | {stats["max_same_channel_urls"]} |

## 推荐订阅地址

| 文件 | 说明 |
|---|---|
| [`cn_tw.m3u`]({RAW_BASE}/cn_tw.m3u) | 全量 M3U，普通播放器优先使用 |
| [`cn_tw.txt`]({RAW_BASE}/cn_tw.txt) | 全量 TXT，TVBox/DIYP 优先使用 |
| [`tv_all.txt`]({RAW_BASE}/tv_all.txt) | 全量 TXT，兼容旧文件名 |
| [`cn_tw_v4.m3u`]({RAW_BASE}/cn_tw_v4.m3u) | IPv4/非 IPv6 M3U |
| [`tv_v4.txt`]({RAW_BASE}/tv_v4.txt) | IPv4/非 IPv6 TXT |
| [`cn_tw_v6.m3u`]({RAW_BASE}/cn_tw_v6.m3u) | IPv6 M3U |
| [`tv_v6.txt`]({RAW_BASE}/tv_v6.txt) | IPv6 TXT |

## 分组统计

| 分组 | 数量 |
|---|---:|
{chr(10).join(group_lines)}

## 上游源状态

| 状态 | 来源 | 解析数量 | 错误 |
|---|---|---:|---|
{chr(10).join(source_lines)}
"""

    atomic_write(path, content)


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
        f'<li><a href="{html.escape(filename)}">{html.escape(filename)}</a> - {html.escape(desc)}</li>'
        for filename, desc in files
    )

    group_rows = "\n".join(
        f"<tr><td>{html.escape(group)}</td><td>{count}</td></tr>"
        for group, count in stats["groups"].items()
    )

    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>mytvlist</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 960px;
      margin: 32px auto;
      padding: 0 16px;
      line-height: 1.7;
    }}
    code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 4px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; }}
    th {{ background: #f6f8fa; }}
  </style>
</head>
<body>
  <h1>mytvlist</h1>
  <p>生成时间：<code>{html.escape(stats["generated_at"])}</code></p>
  <p>频道源数量：<strong>{stats["total"]}</strong>；IPv4/非 IPv6：<strong>{stats["ipv4_or_non_ipv6"]}</strong>；IPv6：<strong>{stats["ipv6"]}</strong></p>

  <h2>文件</h2>
  <ul>
    {links}
  </ul>

  <h2>分类统计</h2>
  <table>
    <tr><th>分类</th><th>数量</th></tr>
    {group_rows}
  </table>

  <p>本项目不托管、不缓存、不转发任何视频内容，仅整理公开网络来源中的文本链接。</p>
</body>
</html>
"""

    atomic_write(path, content)


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
        except Exception as exc:  # noqa: BLE001
            source_status.append({"name": name, "ok": False, "count": 0, "error": str(exc)[:300]})
            log(f"  WARN: {name} failed: {exc}")

    channels = sort_channels(dedupe_channels(all_channels))
    return channels, source_status


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

    if not channels:
        log("No channels generated. Refusing to overwrite existing files.")
        return 2

    channels = sort_channels(channels)
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
