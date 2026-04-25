# mytvlist

自动聚合多个公开 IPTV 列表，并按照固定分类顺序生成适用于 IPTV 播放器、TVBox/DIYP、VLC、TiviMate、APTV 等工具的 `M3U` 与 `TXT` 订阅文件。

> 本项目只聚合公开网络来源中的链接，不托管、不缓存、不转发任何视频内容。频道是否可播放会受地区、运营商、IPv6、上游维护状态、播放器兼容性等因素影响。

## 推荐订阅地址

### 普通 IPTV 播放器

```text
https://raw.githubusercontent.com/ttbly/mytvlist/main/cn_tw.m3u
```

### TVBox / DIYP

```text
https://raw.githubusercontent.com/ttbly/mytvlist/main/cn_tw.txt
```

### IPv4 / IPv6 分流

```text
https://raw.githubusercontent.com/ttbly/mytvlist/main/cn_tw_v4.m3u
https://raw.githubusercontent.com/ttbly/mytvlist/main/cn_tw_v6.m3u
https://raw.githubusercontent.com/ttbly/mytvlist/main/tv_v4.txt
https://raw.githubusercontent.com/ttbly/mytvlist/main/tv_v6.txt
```

## 输出文件说明

| 文件 | 说明 |
|---|---|
| `cn_tw.m3u` | 全量 M3U 订阅，普通播放器优先使用 |
| `cn_tw.txt` | 全量 TXT 订阅，TVBox/DIYP 优先使用 |
| `tv_all.txt` | 全量 TXT 订阅，兼容旧文件名 |
| `tv_v4.txt` | 仅 IPv4/非 IPv6 链接 |
| `tv_v6.txt` | 仅 IPv6 链接 |
| `cn_tw_v4.m3u` | 仅 IPv4/非 IPv6 M3U |
| `cn_tw_v6.m3u` | 仅 IPv6 M3U |
| `stats.json` | 机器可读统计信息 |
| `status.md` | 最近一次更新状态 |
| `index.html` | Docker/Nginx 部署时的 Web 首页 |

## 分类规则

脚本会按照下面的固定顺序输出，不再让分类乱跳：

1. 中央台
2. 卫视
3. 港澳频道
4. 台湾频道
5. 北京频道
6. 天津频道
7. 上海频道
8. 重庆频道
9. 河北频道
10. 山西频道
11. 内蒙古频道
12. 辽宁频道
13. 吉林频道
14. 黑龙江频道
15. 江苏频道
16. 浙江频道
17. 安徽频道
18. 福建频道
19. 江西频道
20. 山东频道
21. 河南频道
22. 湖北频道
23. 湖南频道
24. 广东频道
25. 广西频道
26. 海南频道
27. 四川频道
28. 贵州频道
29. 云南频道
30. 西藏频道
31. 陕西频道
32. 甘肃频道
33. 青海频道
34. 宁夏频道
35. 新疆频道
36. 地方及其他

分类逻辑会优先判断中央台、港澳、台湾、卫视，再判断各省地方台，避免所有地方频道都堆在一个“地方频道”下面。

## 数据来源

默认聚合以下公开来源，并按 URL 去重：

- `iptv-org/iptv`
- `fanmingming/live`
- `YanG-1989/m3u`
- `Guovin/iptv-api`
- `hujingguang/ChinaIPTV`
- `frankwuzp/iptv-cn`

如果某个来源失效，脚本会跳过该来源，不会因为单个上游失败而中断整个更新流程。

## 自动更新

本仓库通过 GitHub Actions 每天自动运行一次：

```text
.github/workflows/update.yml
```

你也可以在 GitHub 仓库页面进入：

```text
Actions -> Update IPTV List -> Run workflow
```

手动运行更新。

手动运行时可以填写：

| 参数 | 说明 |
|---|---|
| `check_streams` | `0` 或 `1`，是否对频道链接做轻量检测。默认 `0` |
| `gh_proxy` | 可选 GitHub Raw 代理前缀，例如 `https://gh-proxy.example.com/` |
| `max_same_channel_urls` | 同一频道最多保留几条线路，默认 `8` |

## 本地运行

```bash
git clone https://github.com/ttbly/mytvlist.git
cd mytvlist
python -m pip install -r requirements.txt
python filter.py
```

如需输出到指定目录：

```bash
OUTPUT_DIR=./data python filter.py
```

如需启用轻量可用性检测：

```bash
CHECK_STREAMS=1 python filter.py
```

如需给 GitHub Raw 增加代理：

```bash
GH_PROXY=https://gh-proxy.example.com/ python filter.py
```

如需调整同一频道最多保留线路数：

```bash
MAX_SAME_CHANNEL_URLS=12 python filter.py
```

## Docker 部署

构建并启动：

```bash
docker compose up -d --build
```

默认本地访问地址：

```text
http://localhost:28024/
```

常用订阅地址：

```text
http://localhost:28024/cn_tw.m3u
http://localhost:28024/cn_tw.txt
http://localhost:28024/tv_v4.txt
http://localhost:28024/tv_v6.txt
```

## 常见问题

### 为什么有些频道不能播放？

可能原因包括：

- 频道源已经失效；
- 频道仅支持 IPv6；
- 频道限制特定地区或运营商；
- 频道需要特定播放器、User-Agent 或 Referer；
- 上游源本身不是 24 小时在线；
- 你的网络无法访问该直播地址。

### 为什么默认不检测所有直播链接？

逐个检测会显著增加运行时间，也可能触发上游限流。

因此默认只聚合和去重，不做全量测速。需要检测时，可以手动运行 Actions 并设置 `check_streams=1`。

### `tv_v4.txt` 是否真的是 IPv4？

是。新版脚本会把包含 IPv6 主机格式的链接分离到 `tv_v6.txt` / `cn_tw_v6.m3u`，`tv_v4.txt` 只保留非 IPv6 链接。

## 免责声明

本项目仅用于学习、研究和个人测试。

本项目不生产、不托管、不缓存、不转发任何视频内容，只对公开网络来源中的文本链接进行整理。

请确保你的使用方式符合所在地法律法规以及相关内容授权要求。

如有任何来源或链接不适合收录，请提交 Issue 或 Pull Request 反馈。
