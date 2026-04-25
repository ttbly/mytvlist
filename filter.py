#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
my-iptv-list 自动聚合脚本

功能：
1. 从多个公开 IPTV M3U/TXT 源抓取频道；
2. 修复原脚本用正则解析 M3U 时容易把 User-Agent 里的逗号误识别为频道名的问题；
3. 自动分类中央台、卫视、港澳台、各省地方台；
4. 生成 TXT、M3U、IPv4、IPv6、统计文件和 Docker Web 首页；
5. 支持 OUTPUT_DIR、GH_PROXY、CHECK_STREAMS 等环境变量。

注意：
- 默认只做“列表级抓取”，不逐个测速。逐个检测会明显变慢，也可能触发上游限流。
- 如需轻量可用性检测，可在 Actions 手动运行时设置 CHECK_STREAMS=1。
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests


# =========================
# 基础配置
# =========================

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ".")).resolve()
GH_PROXY = os.getenv("GH_PROXY", "").strip()
CHECK_STREAMS = os.getenv("CHECK_STREAMS", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
CHECK_TIMEOUT = int(os.getenv("CHECK_TIMEOUT", "8"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "16"))
EPG_URL = os.getenv("EPG_URL", "https://live.fanmingming.cn/e.xml").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "close",
}

STREAM_URL_RE = re.compile(r"^(https?|rtmp|rtsp|rtp|udp|p2p|p3p|mitv)://", re.I)
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    fmt: str = "auto"          # auto / m3u / txt
    keep_all: bool = False     # True 表示该源本身已经按地区过滤，可全部保留
    enabled: bool = True


@dataclass
class Channel:
    name: str
    url: str
    group: str
    source: str
    logo: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    is_ipv6: bool = False
    checked: bool | None = None


# 默认源说明：
# - iptv-org：公开 IPTV 频道库，按国家/地区拆分；
# - fanmingming/live：中文电视/广播图标与相关 M3U 工具源；
# - YanG-1989/m3u：中文圈常用聚合源，Gather.m3u；
# - Guovin/iptv-api：自动采集、筛选、测速后生成的结果源；
# - hujingguang/ChinaIPTV：README 明确说明 cnTV_AutoUpdate.m3u8 会定时更新；
# - frankwuzp/iptv-cn：偏 IPv4，适合补充国内通用/移动源。
SOURCES: list[Source] = [
    Source("IPTV_ORG_CN", "https://iptv-org.github.io/iptv/countries/cn.m3u", keep_all=True),
    Source("IPTV_ORG_HK", "https://iptv-org.github.io/iptv/countries/hk.m3u", keep_all=True),
    Source("IPTV_ORG_MO", "https://iptv-org.github.io/iptv/countries/mo.m3u", keep_all=True),
    Source("IPTV_ORG_TW", "https://iptv-org.github.io/iptv/countries/tw.m3u", keep_all=True),

    Source("FANMINGMING_IPV6", "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u"),
    Source("YANG_GATHER", "https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u"),
    Source("GUOVIN_RESULT", "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.m3u"),

    Source("CHINAIPTV_AUTO", "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8"),
    Source("FRANKWUZP_IPV4_CN", "https://raw.githubusercontent.com/frankwuzp/iptv-cn/main/tv-ipv4-cn.m3u"),
    Source("FRANKWUZP_IPV4_CMCC", "https://raw.githubusercontent.com/frankwuzp/iptv-cn/main/tv-ipv4-cmcc.m3u"),
]


# =========================
# 频道筛选与分类
# =========================

DROP_KEYWORDS = {
    "成人", "午夜", "情色", "福利", "18+", "XXX", "PLAYBOY", "裸", "限制级",
}

CENTRAL_KEYWORDS = {
    "CCTV", "CGTN", "央视", "中央", "中国教育", "CETV", "CHC",
}

HK_MO_KEYWORDS = {
    "香港", "港澳", "澳门", "澳門", "HK", "HONG KONG", "MACAU", "MACAO",
    "TVB", "翡翠", "明珠", "J2", "無綫", "无线", "鳳凰", "凤凰",
    "VIUTV", "VIU", "HOY", "RTHK", "港台", "澳视", "澳視", "莲花", "蓮花",
}

TW_KEYWORDS = {
    "台湾", "台灣", "臺灣", "TW", "TAIWAN",
    "台视", "台視", "中视", "中視", "华视", "華視", "民视", "民視",
    "公视", "公視", "三立", "东森", "東森", "中天", "TVBS",
    "纬来", "緯來", "非凡", "年代", "壹新闻", "壹新聞", "八大",
    "寰宇", "镜新闻", "鏡新聞", "大爱", "大愛",
}

PROVINCES = [
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
]

# 一些不一定包含省名/卫视字样，但通常属于中文频道的关键词
GENERAL_CN_KEYWORDS = {
    "卫视", "衛視", "地方", "新闻", "新聞", "综合", "综艺", "体育", "少儿", "少兒",
    "影视", "电影", "剧场", "电视剧", "财经", "纪录", "纪实", "生活", "都市",
    "法治", "科教", "公共", "农业", "农林", "购物", "卡通", "动漫", "音乐",
    "梨园", "戏曲", "教育", "高清", "4K", "8K", "珠江", "大湾区", "金鹰", "嘉佳",
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    upper = text.upper()
    return any(k.upper() in upper for k in keywords)


def is_unwanted_channel(name: str, group: str = "") -> bool:
    text = f"{name} {group}".upper()
    return any(k.upper() in text for k in DROP_KEYWORDS)


def should_keep_channel(name: str, group: str = "", keep_all: bool = False) -> bool:
    """只保留与大陆、港澳台、中文电视相关的频道。"""
    if not name:
        return False

    if is_unwanted_channel(name, group):
        return False

    if keep_all:
        return True

    text = f"{name} {group}"

    if contains_any(text, CENTRAL_KEYWORDS):
        return True
    if contains_any(text, HK_MO_KEYWORDS):
        return True
    if contains_any(text, TW_KEYWORDS):
        return True
    if contains_any(text, PROVINCES):
        return True
    if contains_any(text, GENERAL_CN_KEYWORDS):
        return True

    return False


def get_group(name: str, source_group: str = "") -> str:
    """根据频道名和上游分组自动归类。"""
    text = f"{name} {source_group}"

    if contains_any(text, CENTRAL_KEYWORDS):
        return "中央台"

    if contains_any(text, HK_MO_KEYWORDS):
        return "港澳频道"

    if contains_any(text, TW_KEYWORDS):
        return "台湾频道"

    if "卫视" in text or "衛視" in text:
        return "卫视"

    for province in PROVINCES:
        if province in text:
            return f"{province}频道"

    return "地方及其他"


GROUP_ORDER = {
    "中央台": 0,
    "卫视": 1,
    "港澳频道": 2,
    "台湾频道": 3,
}
for idx, province in enumerate(PROVINCES, start=10):
    GROUP_ORDER[f"{province}频道"] = idx
GROUP_ORDER["地方及其他"] = 99


# =========================
# URL / M3U / TXT 解析
# =========================

def is_stream_url(line: str) -> bool:
    return bool(STREAM_URL_RE.match(line.strip()))


def clean_url(value: str) -> str:
    url = normalize_text(value)
    url = url.replace("\\", "")

    # 去掉明显的行内注释；保留 URL 内部的 #、?、& 等参数。
    if " " in url:
        url = url.split(" ", 1)[0].strip()

    return url


def is_ipv6_url(url: str) -> bool:
    # 常见 IPv6 URL 形态：http://[2409:...]/xxx
    if "[" in url and "]" in url:
        return True

    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False

    return ":" in host


def parse_extinf_attrs(line: str) -> dict[str, str]:
    return {k.lower(): v.strip() for k, v in ATTR_RE.findall(line)}


def parse_extinf_name(line: str, attrs: dict[str, str]) -> str:
    """
    提取 #EXTINF 行最后的频道名。
    不能简单 split(',')，因为 tvg-logo、User-Agent 等属性里也可能带逗号。
    """
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            return normalize_text(line[i + 1:])

    return normalize_text(attrs.get("tvg-name") or attrs.get("tvg-id") or "")


def parse_m3u(text: str, source: Source) -> list[Channel]:
    channels: list[Channel] = []
    pending: dict[str, str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue

        if line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXTINF"):
            attrs = parse_extinf_attrs(line)
            pending = {
                "name": parse_extinf_name(line, attrs),
                "group": attrs.get("group-title", ""),
                "logo": attrs.get("tvg-logo", ""),
                "tvg_id": attrs.get("tvg-id", ""),
                "tvg_name": attrs.get("tvg-name", ""),
            }
            continue

        if line.startswith("#EXTGRP:"):
            group = normalize_text(line.split(":", 1)[1])
            if pending is not None:
                pending["group"] = group
            continue

        # 播放参数行跳过。它们不能被当成 URL 或频道名。
        if line.startswith("#"):
            continue

        if pending and is_stream_url(line):
            name = pending.get("name") or pending.get("tvg_name") or pending.get("tvg_id")
            source_group = pending.get("group", "")
            url = clean_url(line)

            if should_keep_channel(name, source_group, keep_all=source.keep_all):
                channels.append(
                    Channel(
                        name=name,
                        url=url,
                        group=get_group(name, source_group),
                        source=source.name,
                        logo=pending.get("logo", ""),
                        tvg_id=pending.get("tvg_id", ""),
                        tvg_name=pending.get("tvg_name", ""),
                        is_ipv6=is_ipv6_url(url),
                    )
                )

            pending = None

    return channels


def parse_txt(text: str, source: Source) -> list[Channel]:
    channels: list[Channel] = []
    current_group = ""

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue

        if ",#genre#" in line:
            current_group = normalize_text(line.split(",", 1)[0])
            continue

        if "," not in line:
            continue

        name, url = line.split(",", 1)
        name = normalize_text(name)
        url = clean_url(url)

        if not is_stream_url(url):
            continue

        if should_keep_channel(name, current_group, keep_all=source.keep_all):
            channels.append(
                Channel(
                    name=name,
                    url=url,
                    group=get_group(name, current_group),
                    source=source.name,
                    is_ipv6=is_ipv6_url(url),
                )
            )

    return channels


def parse_playlist(text: str, source: Source) -> list[Channel]:
    if source.fmt == "txt":
        return parse_txt(text, source)
    if source.fmt == "m3u":
        return parse_m3u(text, source)

    if "#EXTINF" in text or text.lstrip().startswith("#EXTM3U"):
        return parse_m3u(text, source)

    return parse_txt(text, source)


# =========================
# 抓取、去重、可选检测
# =========================

def proxied_url(url: str) -> str:
    if not GH_PROXY:
        return url
    return GH_PROXY.rstrip("/") + "/" + url


def candidate_urls(url: str) -> list[str]:
    candidates = [url]

    # 只给 GitHub raw 增加代理候选，避免误代理普通资源站。
    if GH_PROXY and "raw.githubusercontent.com" in url:
        purl = proxied_url(url)
        if purl not in candidates:
            candidates.append(purl)

    return candidates


def fetch_text(session: requests.Session, source: Source) -> str:
    errors: list[str] = []

    for url in candidate_urls(source.url):
        for attempt in range(1, 4):
            try:
                response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200 and response.text.strip():
                    response.encoding = response.apparent_encoding or "utf-8"
                    return response.text

                errors.append(f"{url} HTTP {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url} attempt {attempt}: {exc}")

            time.sleep(1.2 * attempt)

    raise RuntimeError("; ".join(errors[-5:]))


def check_url_alive(url: str) -> bool:
    if not url.lower().startswith(("http://", "https://")):
        # udp/rtmp/rtsp 等不适合用 requests 检测，默认保留。
        return True

    try:
        with requests.get(url, headers=HEADERS, timeout=CHECK_TIMEOUT, stream=True, allow_redirects=True) as response:
            if response.status_code >= 400:
                return False

            # 只读很小一段，避免完整下载。
            for chunk in response.iter_content(chunk_size=1024):
                return bool(chunk) or response.status_code < 400

            return response.status_code < 400
    except Exception:
        return False


def dedupe_channels(channels: Iterable[Channel]) -> list[Channel]:
    """按 URL 去重，来源顺序靠前的保留。"""
    seen_urls: set[str] = set()
    result: list[Channel] = []

    for channel in channels:
        key = channel.url.strip()
        if not key or key in seen_urls:
            continue

        seen_urls.add(key)
        result.append(channel)

    return result


def sort_channels(channels: Iterable[Channel]) -> list[Channel]:
    return sorted(
        channels,
        key=lambda c: (
            GROUP_ORDER.get(c.group, 80),
            c.group,
            c.name.upper(),
            c.source,
            c.url,
        ),
    )


def collect_channels() -> tuple[list[Channel], dict[str, dict[str, int | str]]]:
    session = requests.Session()
    collected: list[Channel] = []
    source_stats: dict[str, dict[str, int | str]] = {}

    for source in SOURCES:
        if not source.enabled:
            continue

        print(f"同步源：{source.name} -> {source.url}")

        try:
            text = fetch_text(session, source)
            parsed = parse_playlist(text, source)
            collected.extend(parsed)
            source_stats[source.name] = {
                "status": "ok",
                "count": len(parsed),
                "url": source.url,
            }
            print(f"  成功：解析到 {len(parsed)} 个频道")
        except Exception as exc:  # noqa: BLE001
            source_stats[source.name] = {
                "status": f"failed: {exc}",
                "count": 0,
                "url": source.url,
            }
            print(f"  跳过：{source.name} 抓取或解析失败：{exc}", file=sys.stderr)

    channels = sort_channels(dedupe_channels(collected))

    if CHECK_STREAMS and channels:
        print(f"开始轻量检测 {len(channels)} 个频道链接，可能需要较长时间...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {executor.submit(check_url_alive, c.url): c for c in channels}
            for future in concurrent.futures.as_completed(future_map):
                channel = future_map[future]
                try:
                    channel.checked = bool(future.result())
                except Exception:
                    channel.checked = False

        before = len(channels)
        channels = [c for c in channels if c.checked is not False]
        print(f"检测完成：保留 {len(channels)} / {before} 个频道")

    return channels, source_stats


# =========================
# 输出
# =========================

def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8", newline="\n")
    tmp_path.replace(path)


def render_txt(channels: Iterable[Channel]) -> str:
    lines: list[str] = []
    current_group = None

    for channel in channels:
        if channel.group != current_group:
            current_group = channel.group
            lines.append(f"{current_group},#genre#")

        tag = " (IPv6)" if channel.is_ipv6 else ""
        lines.append(f"{channel.name}{tag},{channel.url}")

    return "\n".join(lines) + "\n"


def xml_escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def render_m3u(channels: Iterable[Channel]) -> str:
    header = "#EXTM3U"
    if EPG_URL:
        header += f' x-tvg-url="{xml_escape(EPG_URL)}"'

    lines = [header]

    for channel in channels:
        tag = " (IPv6)" if channel.is_ipv6 else ""
        attrs = [
            f'group-title="{xml_escape(channel.group)}"',
            f'tvg-name="{xml_escape(channel.tvg_name or channel.name)}"',
        ]

        if channel.tvg_id:
            attrs.append(f'tvg-id="{xml_escape(channel.tvg_id)}"')
        if channel.logo:
            attrs.append(f'tvg-logo="{xml_escape(channel.logo)}"')

        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{channel.name}{tag}')
        lines.append(channel.url)

    return "\n".join(lines) + "\n"


def render_index(generated_at: str, channels: list[Channel]) -> str:
    group_counter = Counter(c.group for c in channels)
    rows = "\n".join(
        f"<tr><td>{html.escape(group)}</td><td>{count}</td></tr>"
        for group, count in group_counter.most_common()
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>my-iptv-list</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; line-height: 1.65; }}
    code {{ background: #f4f4f4; padding: .15rem .35rem; border-radius: .25rem; }}
    table {{ border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ border: 1px solid #ddd; padding: .45rem .75rem; }}
  </style>
</head>
<body>
  <h1>my-iptv-list</h1>
  <p>更新时间：<code>{html.escape(generated_at)}</code></p>
  <p>频道数量：<strong>{len(channels)}</strong></p>

  <h2>订阅文件</h2>
  <ul>
    <li><a href="cn_tw.m3u">cn_tw.m3u</a></li>
    <li><a href="cn_tw.txt">cn_tw.txt</a></li>
    <li><a href="tv_all.txt">tv_all.txt</a></li>
    <li><a href="tv_v4.txt">tv_v4.txt</a></li>
    <li><a href="tv_v6.txt">tv_v6.txt</a></li>
    <li><a href="stats.json">stats.json</a></li>
    <li><a href="status.md">status.md</a></li>
  </ul>

  <h2>分组统计</h2>
  <table>
    <thead><tr><th>分组</th><th>频道数</th></tr></thead>
    <tbody>
      {rows}
    </tbody>
  </table>

  <p>说明：本项目仅聚合公开网络来源，不托管、不缓存任何视频内容。</p>
</body>
</html>
"""


def render_status(generated_at: str, channels: list[Channel], source_stats: dict[str, dict[str, int | str]]) -> str:
    group_counter = Counter(c.group for c in channels)
    source_counter = Counter(c.source for c in channels)

    lines = [
        "# IPTV 更新状态",
        "",
        f"- 更新时间：`{generated_at}`",
        f"- 频道总数：`{len(channels)}`",
        f"- IPv4 数量：`{sum(1 for c in channels if not c.is_ipv6)}`",
        f"- IPv6 数量：`{sum(1 for c in channels if c.is_ipv6)}`",
        f"- 是否启用链接检测：`{CHECK_STREAMS}`",
        "",
        "## 输出文件",
        "",
        "| 文件 | 说明 |",
        "|---|---|",
        "| `cn_tw.m3u` | 全量 M3U 订阅 |",
        "| `cn_tw.txt` | 全量 TXT 订阅 |",
        "| `tv_all.txt` | 全量 TXT 订阅，兼容旧文件名 |",
        "| `tv_v4.txt` | 仅 IPv4/非 IPv6 链接 |",
        "| `tv_v6.txt` | 仅 IPv6 链接 |",
        "| `cn_tw_v4.m3u` | 仅 IPv4/非 IPv6 M3U |",
        "| `cn_tw_v6.m3u` | 仅 IPv6 M3U |",
        "| `stats.json` | 机器可读统计信息 |",
        "",
        "## 分组统计",
        "",
        "| 分组 | 数量 |",
        "|---|---:|",
    ]

    for group, count in group_counter.most_common():
        lines.append(f"| {group} | {count} |")

    lines.extend([
        "",
        "## 来源统计",
        "",
        "| 来源 | 本次解析数 | 最终保留数 | 状态 |",
        "|---|---:|---:|---|",
    ])

    for source in SOURCES:
        stats = source_stats.get(source.name, {"count": 0, "status": "not_run"})
        lines.append(
            f"| {source.name} | {stats.get('count', 0)} | {source_counter.get(source.name, 0)} | {stats.get('status', '')} |"
        )

    lines.append("")
    return "\n".join(lines)


def write_outputs(channels: list[Channel], source_stats: dict[str, dict[str, int | str]]) -> None:
    ensure_output_dir()

    generated_at = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")

    v4_channels = [c for c in channels if not c.is_ipv6]
    v6_channels = [c for c in channels if c.is_ipv6]

    outputs = {
        "cn_tw.txt": render_txt(channels),
        "tv_all.txt": render_txt(channels),
        "tv_v4.txt": render_txt(v4_channels),
        "tv_v6.txt": render_txt(v6_channels),
        "cn_tw.m3u": render_m3u(channels),
        "cn_tw_v4.m3u": render_m3u(v4_channels),
        "cn_tw_v6.m3u": render_m3u(v6_channels),
        "index.html": render_index(generated_at, channels),
        "status.md": render_status(generated_at, channels, source_stats),
    }

    for filename, content in outputs.items():
        atomic_write(OUTPUT_DIR / filename, content)

    stats = {
        "generated_at": generated_at,
        "total": len(channels),
        "ipv4": len(v4_channels),
        "ipv6": len(v6_channels),
        "groups": dict(Counter(c.group for c in channels)),
        "sources": source_stats,
        "final_sources": dict(Counter(c.source for c in channels)),
        "files": list(outputs.keys()),
    }
    atomic_write(OUTPUT_DIR / "stats.json", json.dumps(stats, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    print(f"输出目录：{OUTPUT_DIR}")
    print(f"GitHub Raw 代理：{'已启用' if GH_PROXY else '未启用'}")
    print(f"链接检测：{'已启用' if CHECK_STREAMS else '未启用'}")

    channels, source_stats = collect_channels()
    if not channels:
        print("没有解析到任何频道，拒绝覆盖旧文件。", file=sys.stderr)
        return 2

    write_outputs(channels, source_stats)

    print(f"同步完成：共生成 {len(channels)} 个频道。")
    print(f"输出文件已写入：{OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
