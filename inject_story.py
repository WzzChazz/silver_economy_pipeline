import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.core import database

def inject_mock_story():
    database.init_db()
    scores = {"pain": 20, "truth": 20, "resonance": 20, "freshness": 15, "rewrite": 20}
    database.insert_story(
        theme="老伴故事",
        title="老伴的日记本",
        story="今天打扫卫生，翻出了老伴十年前写的日记。里面写着：老婆喜欢吃排骨，我以后中午在厂里不吃肉了，省点钱周末给她买排骨。看完我瞬间泪奔。",
        emotion="感动",
        source="用户投稿",
        narrative_type="回忆型",
        scene="home",
        persona="温和中老年叙述者",
        scores=scores
    )
    print("✅ 成功注入一条故事素材！")

if __name__ == "__main__":
    inject_mock_story()
