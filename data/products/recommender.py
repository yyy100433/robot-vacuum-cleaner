#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫地机器人推荐引擎模块 (LangChain 版本)
"""

import sqlite3
import os
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

@dataclass
class Product:
    """产品信息"""
    id: int
    brand: str
    model: str
    suction_power: int
    navigation_type: str
    battery_life: int
    dustbin_capacity: float
    water_tank_capacity: int
    mopping_function: bool
    self_charging: bool
    virtual_wall: bool
    app_control: bool
    voice_control: bool
    noise_level: int
    cleaning_area: int
    price: float
    rating: float
    review_count: int
    weight: float
    height: float
    release_year: int

class VacuumRobotRecommender:
    """扫地机器人推荐引擎"""

    def __init__(self, db_path: str = None):
        """初始化推荐引擎"""
        if db_path is None:
            # 默认使用 langchain-agent/data/products/robot_vacuum.db
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, 'robot_vacuum.db')
        self.db_path = db_path
        self.conn = None
        self.cursor = None

    def connect(self):
        """连接到数据库"""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"数据库文件不存在：{self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
            self.cursor = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_all_products(self) -> List[Product]:
        """获取所有产品"""
        self.cursor.execute("SELECT * FROM products")
        products = []
        for row in self.cursor.fetchall():
            product = Product(
                id=row['id'], brand=row['brand'], model=row['model'],
                suction_power=row['suction_power'],
                navigation_type=row['navigation_type'],
                battery_life=row['battery_life'],
                dustbin_capacity=row['dustbin_capacity'],
                water_tank_capacity=row['water_tank_capacity'],
                mopping_function=bool(row['mopping_function']),
                self_charging=bool(row['self_charging']),
                virtual_wall=bool(row['virtual_wall']),
                app_control=bool(row['app_control']),
                voice_control=bool(row['voice_control']),
                noise_level=row['noise_level'],
                cleaning_area=row['cleaning_area'],
                price=row['price'],
                rating=row['rating'],
                review_count=row['review_count'],
                weight=row['weight'],
                height=row['height'],
                release_year=row['release_year']
            )
            products.append(product)
        return products

    def recommend_by_criteria(self, brand: Optional[str] = None,
                              min_suction: int = 1500,
                              navigation: Optional[str] = None,
                              max_price: float = 5000,
                              min_battery: int = 60,
                              house_area: int = 100,
                              must_have_mopping: bool = False,
                              must_have_self_charging: bool = False,
                              limit: int = 5) -> List[Dict]:
        """根据条件推荐产品"""

        # 构建 SQL 查询
        conditions = ["1=1"]
        params = []

        if brand:
            conditions.append("brand = ?")
            params.append(brand)

        conditions.append("suction_power >= ?")
        params.append(min_suction)

        if navigation:
            if "激光" in navigation or "LDS" in navigation:
                conditions.append("(navigation_type LIKE '%激光%' OR navigation_type LIKE '%LDS%')")
            elif "视觉" in navigation:
                conditions.append("navigation_type LIKE '%视觉%'")
            else:
                conditions.append("navigation_type LIKE ?")
                params.append(f"%{navigation}%")

        conditions.append("price <= ?")
        params.append(max_price)

        conditions.append("battery_life >= ?")
        params.append(min_battery)

        conditions.append("cleaning_area >= ?")
        params.append(house_area)

        if must_have_mopping:
            conditions.append("mopping_function = 1")

        if must_have_self_charging:
            conditions.append("self_charging = 1")

        sql = f"""
            SELECT * FROM products
            WHERE {' AND '.join(conditions)}
            ORDER BY rating DESC, suction_power DESC, price ASC
            LIMIT ?
        """
        params.append(limit)

        self.cursor.execute(sql, params)
        results = []

        for row in self.cursor.fetchall():
            # 计算匹配分数
            score = self._calculate_score(row, brand, min_suction, navigation, max_price, house_area)

            result = {
                'brand': row['brand'],
                'model': row['model'],
                'suction_power': row['suction_power'],
                'navigation_type': row['navigation_type'],
                'battery_life': row['battery_life'],
                'cleaning_area': row['cleaning_area'],
                'price': row['price'],
                'rating': row['rating'],
                'review_count': row['review_count'],
                'mopping_function': '支持' if row['mopping_function'] else '不支持',
                'self_charging': '支持' if row['self_charging'] else '不支持',
                'noise_level': row['noise_level'],
                'match_score': score
            }
            results.append(result)

        return results

    def _calculate_score(self, row, brand, min_suction, navigation, max_price, house_area) -> float:
        """计算匹配分数 (0-1)"""
        score = 0.0

        # 品牌匹配 (20%)
        if brand and row['brand'] == brand:
            score += 0.20
        elif not brand:
            score += 0.10

        # 吸力匹配 (25%)
        suction_ratio = (row['suction_power'] - min_suction) / 2000
        score += min(0.25, 0.10 + suction_ratio * 0.15)

        # 评分匹配 (20%)
        score += (row['rating'] / 5.0) * 0.20

        # 价格匹配 (20%)
        if row['price'] <= max_price:
            price_ratio = (max_price - row['price']) / max_price
            score += 0.10 + (price_ratio * 0.10)

        # 面积匹配 (15%)
        if row['cleaning_area'] >= house_area:
            score += 0.15

        return min(1.0, score)

    def get_brands(self) -> List[str]:
        """获取所有品牌列表"""
        self.cursor.execute("SELECT DISTINCT brand FROM products ORDER BY brand")
        return [row[0] for row in self.cursor.fetchall()]

    def get_product_count(self) -> int:
        """获取产品总数"""
        self.cursor.execute("SELECT COUNT(*) FROM products")
        return self.cursor.fetchone()[0]