"""
assets_fetcher.py  —  公共版权(PD/CC0)老物件素材下载器

用途：为「老物件年代记忆」线抓取**可商用**的真实物件图,落到
  assets/brolls/年代记忆/<slug>/*.jpg
供 video_engine 的 _pick_background(theme="年代记忆", scene=slug) 直接取用。

合规要点：
  - 只走 Openverse 官方聚合 API(汇集 Wikimedia / 博物馆 / Flickr 等)。
  - 只取 license=cc0,pdm(公共领域/CC0),可商用、无需署名;仍记录来源到 sources.txt。
  - 用英文检索词提召回(公共库以英文索引为主)。
  - 守限速、带 UA、失败跳过。

诚实边界：公共库的「中国年代」物件偏少,返回的多为通用/西式 vintage 物件,
只适合当**氛围素材**。核心情感素材(真照片真故事)仍要靠 UGC,本工具替代不了。
"""

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BROLLS_DIR = os.path.join(BASE_DIR, os.getenv("ASSETS_DIR", "assets"), "brolls")
ERA_THEME = "年代记忆"

OPENVERSE_API = "https://api.openverse.org/v1/images/"
_UA = "silver-economy-pipeline/1.0 (asset fetcher; respectful use)"

# 老物件登记表：slug 同时用于 检索 与 brolls 子目录 与 video_engine 选题
#   cn=中文名 era=唤起年代 en=英文检索词(提召回) sensory=可写进文案的细节
OBJECT_REGISTRY = [
    {"slug": "enamel_mug",     "cn": "搪瓷缸",     "era": "六七十年代", "en": ["vintage enamel mug", "old enamel cup"],          "sensory": "掉瓷的边、红双喜字样"},
    {"slug": "ration_coupon",  "cn": "粮票",       "era": "计划经济年代", "en": ["ration coupon", "vintage food stamp paper"],     "sensory": "薄薄一张、印着面额"},
    {"slug": "wall_calendar",  "cn": "老挂历",     "era": "八十年代",   "en": ["vintage wall calendar", "old chinese calendar"],   "sensory": "翻到某一页、红圈的日子"},
    {"slug": "sewing_machine", "cn": "缝纫机",     "era": "七八十年代", "en": ["vintage sewing machine", "old treadle sewing machine"], "sensory": "脚踏板、上海牌"},
    {"slug": "radio",          "cn": "老式收音机", "era": "七八十年代", "en": ["vintage radio", "old transistor radio"],          "sensory": "拉杆天线、沙沙的电流声"},
    {"slug": "kerosene_lamp",  "cn": "煤油灯",     "era": "六七十年代", "en": ["kerosene lamp", "vintage oil lamp"],              "sensory": "玻璃罩、昏黄的光"},
    {"slug": "bicycle",        "cn": "二八自行车", "era": "八十年代",   "en": ["vintage bicycle black", "old chinese bicycle"],    "sensory": "二八大杠、后座带人"},
    {"slug": "tin_box",        "cn": "铁皮饼干盒", "era": "八九十年代", "en": ["vintage tin biscuit box", "old metal candy tin"],  "sensory": "装着针线、不装饼干"},
    {"slug": "mantel_clock",   "cn": "老座钟",     "era": "八十年代",   "en": ["vintage mantel clock", "old wind up clock"],       "sensory": "整点报时、嘀嗒声"},
    {"slug": "thermos",        "cn": "竹壳热水瓶", "era": "七八十年代", "en": ["vintage thermos flask", "bamboo thermos bottle"],  "sensory": "竹编外壳、牡丹花"},
]

_REGISTRY_BY_SLUG = {o["slug"]: o for o in OBJECT_REGISTRY}


def _download_one(client: httpx.Client, url: str, dest: str) -> bool:
    try:
        r = client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        if not r.content or len(r.content) < 2000:  # 跳过空/缩略图
            return False
        with open(dest, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        logger.debug(f"下载失败 {url}: {e}")
        return False


def fetch_for_object(client: httpx.Client, obj: dict, per_object: int, out_root: str) -> int:
    """抓单个老物件的 PD/CC0 图，落到 out_root/<slug>/。返回成功张数。"""
    slug = obj["slug"]
    out_dir = os.path.join(out_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    existing = len([f for f in os.listdir(out_dir) if f.endswith(".jpg")])
    if existing >= per_object:
        logger.info(f"[{obj['cn']}] 已有 {existing} 张，跳过")
        return 0

    got = 0
    sources = []
    for term in obj["en"]:
        if got >= per_object:
            break
        try:
            resp = client.get(
                OPENVERSE_API,
                params={"q": term, "license": "cc0,pdm", "page_size": 20},
                timeout=30,
            )
            if resp.status_code == 429:
                logger.warning("Openverse 限速(429)，等待 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as e:
            logger.warning(f"[{obj['cn']}] 检索 '{term}' 失败：{e}")
            continue

        for item in results:
            if got >= per_object:
                break
            img_url = item.get("url")
            if not img_url:
                continue
            dest = os.path.join(out_dir, f"{slug}_{item.get('id', got)}.jpg")
            if os.path.exists(dest):
                continue
            if _download_one(client, img_url, dest):
                got += 1
                sources.append(
                    f"{os.path.basename(dest)}\t{item.get('license','')}-{item.get('license_version','')}"
                    f"\t{item.get('source','')}\t{item.get('foreign_landing_url','')}"
                )
            time.sleep(0.5)  # 温柔限速

    if sources:
        with open(os.path.join(out_dir, "sources.txt"), "a", encoding="utf-8") as f:
            f.write("\n".join(sources) + "\n")
    logger.info(f"[{obj['cn']}] 新增 {got} 张 → {out_dir}")
    return got


def fetch_image_assets(slugs: Optional[list] = None, per_object: int = 5,
                       out_root: Optional[str] = None) -> int:
    """主入口：批量抓老物件 PD/CC0 图。slugs 缺省抓全部登记物件。"""
    out_root = out_root or os.path.join(BROLLS_DIR, ERA_THEME)
    os.makedirs(out_root, exist_ok=True)
    objects = [_REGISTRY_BY_SLUG[s] for s in slugs if s in _REGISTRY_BY_SLUG] if slugs else OBJECT_REGISTRY

    logger.info(f"开始抓取公共版权老物件图：{len(objects)} 类 × 每类 {per_object} 张 → {out_root}")
    total = 0
    with httpx.Client(headers={"User-Agent": _UA}) as client:
        for obj in objects:
            total += fetch_for_object(client, obj, per_object, out_root)
            time.sleep(1.0)
    logger.info(f"抓取完成，共新增 {total} 张。注意：公共库以通用/西式 vintage 为主，仅作氛围素材。")
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    fetch_image_assets(per_object=5)
