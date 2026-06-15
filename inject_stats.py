import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.core import database

database.init_db()

res1 = database.update_production_stats("昨天去医院撞见", 146, 0, 0, 0)
res2 = database.update_production_stats("人到晚年", 6001, 15, 3, 10)

print(f"Update 1: {res1}")
print(f"Update 2: {res2}")
