#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coze 智能体回答解析器 - 将 Coze 返回的扫地机器人推荐解析并存储到数据库
"""

import re
import sqlite3
import json
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import os


@dataclass
class VacuumRobot:
    """扫地机器人数据类"""
    brand: str
    model: str
    price: Optional[int] = None
    suction_power: Optional[int] = None
    battery_life: Optional[int] = None
    cleaning_area: Optional[int] = None
    mopping_function: Optional[bool] = None
    self_charging: Optional[bool] = None
    navigation_type: Optional[str] = None
    rating: Optional[float] = None
    noise_level: Optional[int] = None
    dustbin_capacity: Optional[float] = None
    water_tank_capacity: Optional[float] = None
    virtual_wall: Optional[bool] = None
    app_control: Optional[bool] = None
    voice_control: Optional[bool] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    release_year: Optional[int] = None
    review_count: Optional[int] = None
    description: Optional[str] = None  # 推荐理由
    pros: Optional[str] = None  # 优点
    cons: Optional[str] = None  # 缺点


class CozeResponseParser:
    """解析 Coze 智能体的回答"""

    def __init__(self):
        # 品牌列表（用于识别）
        self.brands = ['小米', '米家', '石头', '科沃斯', '云鲸', '追觅', 'iRobot', '美的', '海尔',
                       '360', '浦桑尼克', 'Neato', '戴森', '飞利浦', '松下', 'LG', '三星']

    def parse(self, coze_response: str) -> List[VacuumRobot]:
        """
        解析 Coze 返回的推荐文本

        示例输入格式：
        为您推荐 3 款符合条件的产品

        推荐 #1：石头 P10
        价格：3299 元
        吸力：5500 Pa
        续航：180 分钟
        适用面积：150 ㎡
        拖地功能：支持
        用户评分：4.6/5

        为什么适合您：
        120平大户型可一次覆盖全屋清洁...

        需要注意：
        对短绒地毯的识别偶尔存在误判...
        """
        robots = []

        # 分割出每个推荐项
        # 匹配 "推荐 #数字" 或 "【推荐 #数字】" 开头的区块
        pattern = r'(?:【推荐|推荐)\s*#(\d+)】?\s*[：:]\s*(.+?)(?=(?:(?:【推荐|推荐)\s*#\d+】?\s*[：:])|$)'

        # 更简单的方式：按推荐编号分割
        sections = re.split(r'(?:^|\n)(?:【?推荐\s*#(\d+)】?\s*[：:]\s*)', coze_response)

        if len(sections) > 1:
            # sections[0] 是开头文字，sections[1] 是第一个编号，sections[2] 是第一个内容，以此类推
            for i in range(1, len(sections), 2):
                if i + 1 < len(sections):
                    robot = self._parse_single_product(sections[i + 1])
                    if robot:
                        robots.append(robot)

        return robots

    def _parse_single_product(self, text: str) -> Optional[VacuumRobot]:
        """解析单个产品信息"""
        lines = text.strip().split('\n')
        if not lines:
            return None

        # 第一行应该是品牌和型号
        first_line = lines[0].strip()
        brand, model = self._extract_brand_model(first_line)

        if not brand or not model:
            return None

        robot = VacuumRobot(brand=brand, model=model)

        # 解析其他字段
        full_text = text

        # 价格
        price_match = re.search(r'价[格价]\s*[：:]\s*(\d+)', full_text)
        if price_match:
            robot.price = int(price_match.group(1))

        # 吸力
        suction_match = re.search(r'吸[力率]\s*[：:]\s*(\d+)', full_text)
        if suction_match:
            robot.suction_power = int(suction_match.group(1))

        # 续航
        battery_match = re.search(r'续[航航]\s*[：:]\s*(\d+)', full_text)
        if battery_match:
            robot.battery_life = int(battery_match.group(1))

        # 适用面积
        area_match = re.search(r'适用面积\s*[：:]\s*(\d+)', full_text)
        if area_match:
            robot.cleaning_area = int(area_match.group(1))

        # 拖地功能
        if '拖地' in full_text:
            if re.search(r'拖地[功能]*\s*[：:]\s*支持|有.*拖|支持.*拖', full_text):
                robot.mopping_function = True
            elif re.search(r'拖地[功能]*\s*[：:]\s*不支持|无.*拖', full_text):
                robot.mopping_function = False

        # 自动回充
        if '回充' in full_text or '充电' in full_text:
            if re.search(r'回充|自动.*充电|支持.*回充', full_text):
                robot.self_charging = True

        # 导航类型
        nav_types = ['LDS激光', '激光导航', '视觉导航', '视觉', 'dToF', '陀螺仪', '随机']
        for nav in nav_types:
            if nav in full_text:
                robot.navigation_type = nav
                break

        # 评分
        rating_match = re.search(r'评分\s*[：:]\s*(\d+\.?\d*)', full_text)
        if rating_match:
            robot.rating = float(rating_match.group(1))

        # 推荐理由（为什么适合您部分）
        why_match = re.search(r'为什么适合[您你][：:\n]+(.+?)(?=需要注意|缺点|$)', full_text, re.DOTALL)
        if why_match:
            robot.description = why_match.group(1).strip()

        # 缺点/注意事项
        cons_match = re.search(r'需要注意[：:\n]+(.+?)(?=\n\n|$)', full_text, re.DOTALL)
        if cons_match:
            robot.cons = cons_match.group(1).strip()

        return robot

    def _extract_brand_model(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """从文本中提取品牌和型号"""
        # 移除可能的标记符号
        text = re.sub(r'^[【\[]?', '', text)
        text = re.sub(r'[】\]]?$', '', text)
        text = text.strip()

        for brand in self.brands:
            if brand in text:
                # 品牌后面的内容作为型号
                idx = text.find(brand)
                model = text[idx + len(brand):].strip()
                # 移除可能的分隔符
                model = re.sub(r'^[\s：:]+', '', model)
                return brand, model if model else f"{brand}扫地机器人"

        # 如果没找到已知品牌，尝试通用匹配
        parts = text.split(None, 1)
        if len(parts) >= 2:
            return parts[0], parts[1]

        return None, None


class CozeProductStorage:
    """将解析的产品存储到数据库"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 默认路径
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(base_dir, 'data', 'products', 'robot_vacuum.db')

        self.db_path = db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """确保数据库文件存在"""
        if not os.path.exists(self.db_path):
            # 创建目录
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            # 初始化数据库
            self._init_database()

    def _init_database(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand VARCHAR(50) NOT NULL,
            model VARCHAR(100) NOT NULL,
            suction_power INTEGER,
            navigation_type VARCHAR(50),
            battery_life INTEGER,
            dustbin_capacity REAL,
            water_tank_capacity REAL,
            mopping_function BOOLEAN,
            self_charging BOOLEAN,
            virtual_wall BOOLEAN,
            app_control BOOLEAN,
            voice_control BOOLEAN,
            noise_level INTEGER,
            cleaning_area INTEGER,
            price REAL,
            rating REAL,
            review_count INTEGER,
            weight REAL,
            height REAL,
            release_year INTEGER,
            description TEXT,
            pros TEXT,
            cons TEXT,
            source VARCHAR(50) DEFAULT 'coze',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_source ON products(source)")

        conn.commit()
        conn.close()
        print(f"数据库已初始化: {self.db_path}")

    def save_robots(self, robots: List[VacuumRobot], skip_duplicates: bool = True) -> Dict[str, int]:
        """
        保存扫地机器人列表到数据库

        Args:
            robots: VacuumRobot 对象列表
            skip_duplicates: 是否跳过重复项（基于 brand + model）

        Returns:
            统计信息 dict: {'inserted': int, 'skipped': int, 'errors': int}
        """
        stats = {'inserted': 0, 'skipped': 0, 'errors': 0}

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        for robot in robots:
            try:
                # 检查是否已存在
                if skip_duplicates:
                    cursor.execute(
                        "SELECT id FROM products WHERE brand = ? AND model = ?",
                        (robot.brand, robot.model)
                    )
                    if cursor.fetchone():
                        stats['skipped'] += 1
                        print(f"  跳过重复: {robot.brand} {robot.model}")
                        continue

                # 插入数据
                cursor.execute("""
                INSERT INTO products (
                    brand, model, suction_power, navigation_type, battery_life,
                    dustbin_capacity, water_tank_capacity, mopping_function,
                    self_charging, virtual_wall, app_control, voice_control,
                    noise_level, cleaning_area, price, rating, review_count,
                    weight, height, release_year, description, pros, cons, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    robot.brand, robot.model, robot.suction_power, robot.navigation_type,
                    robot.battery_life, robot.dustbin_capacity, robot.water_tank_capacity,
                    robot.mopping_function, robot.self_charging, robot.virtual_wall,
                    robot.app_control, robot.voice_control, robot.noise_level,
                    robot.cleaning_area, robot.price, robot.rating, robot.review_count,
                    robot.weight, robot.height, robot.release_year, robot.description,
                    robot.pros, robot.cons, 'coze'
                ))

                stats['inserted'] += 1
                print(f"  已插入: {robot.brand} {robot.model}")

            except Exception as e:
                stats['errors'] += 1
                print(f"  错误 ({robot.brand} {robot.model}): {e}")

        conn.commit()
        conn.close()

        return stats

    def get_all_products(self) -> List[Dict]:
        """获取所有产品"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM products WHERE source = 'coze' ORDER BY created_at DESC")
        products = [dict(row) for row in cursor.fetchall()]

        conn.close()
        return products


def parse_and_save_coze_response(coze_response: str, db_path: Optional[str] = None) -> Dict[str, int]:
    """
    主函数：解析 Coze 响应并保存到数据库

    Args:
        coze_response: Coze 智能体返回的文本
        db_path: 数据库路径（可选）

    Returns:
        统计信息
    """
    print("=" * 60)
    print("开始解析 Coze 响应...")
    print("=" * 60)

    # 1. 解析响应
    parser = CozeResponseParser()
    robots = parser.parse(coze_response)

    print(f"\n解析完成，共找到 {len(robots)} 个产品")

    if not robots:
        print("未解析到任何产品信息，请检查输入格式")
        return {'inserted': 0, 'skipped': 0, 'errors': 0}

    # 显示解析结果
    print("\n解析结果预览：")
    for i, robot in enumerate(robots, 1):
        print(f"\n  [{i}] {robot.brand} {robot.model}")
        if robot.price:
            print(f"      价格: {robot.price}元")
        if robot.suction_power:
            print(f"      吸力: {robot.suction_power}Pa")
        if robot.battery_life:
            print(f"      续航: {robot.battery_life}分钟")

    # 2. 保存到数据库
    print("\n" + "=" * 60)
    print("开始保存到数据库...")
    print("=" * 60)

    storage = CozeProductStorage(db_path)
    stats = storage.save_robots(robots)

    print("\n" + "=" * 60)
    print("处理完成！")
    print(f"  成功插入: {stats['inserted']} 条")
    print(f"  跳过重复: {stats['skipped']} 条")
    print(f"  错误: {stats['errors']} 条")
    print("=" * 60)

    return stats


# 使用示例
if __name__ == "__main__":
    # 示例 Coze 响应文本
    sample_response = """
为您推荐 2 款符合条件的产品

推荐 #1：石头 P10 Pro
价格：3999 元
吸力：7000 Pa
续航：180 分钟
适用面积：150 ㎡
拖地功能：支持
用户评分：4.7/5

为什么适合您：
120平大户型可一次覆盖全屋清洁，无需中途充电；配备全软胶主刷，贴合木地板纹理清洁同时避免刮伤漆面；针对宠物毛发设计的全胶刷彻底杜绝毛发缠绕。

需要注意：
价格相对较高，基站体积较大需要预留足够空间。

推荐 #2：科沃斯 T20 Pro
价格：3599 元
吸力：6000 Pa
续航：160 分钟
适用面积：120 ㎡
拖地功能：支持
用户评分：4.6/5

为什么适合您：
具备热水洗拖布功能，清洁效果更好；支持地毯识别自动抬升拖布，适合有地毯的家庭。

需要注意：
续航相对较短，大户型可能需要中途回充。
"""

    # 解析并保存
    result = parse_and_save_coze_response(sample_response)
    print(f"\n结果: {result}")
