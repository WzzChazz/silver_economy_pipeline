# 银发矩阵 · Silver Economy Pipeline

面向微信视频号「银发市场（50–75 岁）」的短视频自动化生产流水线：
**采集情感故事 → AI 五维评分入库 → 改写爆款脚本 → TTS+字幕+FFmpeg 合成竖屏视频 → 数据回传 → 主题权重飞轮**。

## 架构

```
scripts/
  main.py            # CLI 入口（setup / crawl / produce / report / schedule / health / scrape）
  monitor.py         # 统一日志 + 企微告警 + 健康检查
src/
  core/
    database.py      # SQLite 数据层（故事库 / 生产记录 / 账号矩阵 / 主题飞轮）
    llm.py           # 统一 LLM 调用层（OpenAI 兼容，DeepSeek↔Script 双供应商兜底）
  engines/
    crawler_engine.py  # 知乎/小红书/微信 多渠道采集（Playwright，单渠道复用浏览器）
    story_engine.py    # 五维评分 + 语义去重入库
    video_engine.py    # 脚本改写 + edge-tts + Whisper 字幕 + FFmpeg 合成 + A/B 封面
  api/
    performance_api.py # 视频号 Open API 数据回传 + 选题报告
    channels_rpa.py    # 视频号创作者中心 RPA 抓数（Open API 无权限时的平替）
scratch/             # 实验/一次性脚本（已 gitignore，不属于主流程）
```

## 数据流闭环（飞轮）

1. `crawler_engine` 采集原始故事 → `story_engine.evaluate_story` 五维评分（≥75 且痛点/真实≥13 入库）。
2. `video_engine` 按人设/主题加权拉取故事 → 改写脚本 → 合成视频 → 写 `production_history`。
3. `performance_api`（或 `channels_rpa`）回传播放/点赞/完播率 → `update_performance` 更新
   `theme_performance`，并据完播率归一化回写 `story_pool.theme_weight`，影响下一轮选题排序。

> 网页 RPA 抓不到完播率时走 `watch_rate=None` 分支，只更新点赞侧信号，**不会**把对应主题权重误归零。

## 快速开始

```bash
pip install -r requirements.txt
playwright install chromium          # 采集/RPA 需要
cp .env.example .env                 # 填入 API Key

python scripts/main.py setup         # 初始化数据库 + 3 个示例账号
python scripts/main.py crawl         # 采集一次
python scripts/main.py produce       # 为活跃账号各生成一条视频
python scripts/main.py report        # 选题效果报告
python scripts/main.py health        # 故事库健康状态
python scripts/main.py scrape        # 本地 RPA 抓视频号数据回写
python scripts/main.py schedule      # 启动调度器（每日采集 + 数据回传）
```

## 环境变量

见 [.env.example](.env.example)。要点：

- `DEEPSEEK_*`：故事评分（便宜），`SCRIPT_*`：文案改写（强）。任一未配置会自动兜底到另一家。
- `ZHIPU_API_KEY`：CogView 背景图，缺省时回退本地素材 / Unsplash 兜底图。
- `TTS_VOICE_MODEL` / `TTS_VOICE_MODEL_FEMALE`：edge-tts 男/女声。
- `WEIXIN_APPID` / `WEIXIN_SECRET`：视频号 Open API 数据回传（无权限时改用 `scrape`）。
- `WECOM_WEBHOOK_URL`：企微告警（可选）。`PROXY_LIST`：代理池（逗号分隔，可选）。
- `CHANNELS_RPA_DEBUG=1`：RPA 时落盘页面源码/截图用于调试 DOM。

## 依赖

Python 3.10+、FFmpeg（系统安装）。语义去重需要 `sentence-transformers`（torch），
不可用时自动降级为 difflib 字面去重；Whisper 不可用时降级为均匀分配字幕时间戳。
