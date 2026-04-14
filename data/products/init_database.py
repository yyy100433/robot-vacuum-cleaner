#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫地机器人推荐系统 - 数据库初始化脚本 (LangChain 版本)
"""

import sqlite3
import os
import random

def create_database():
    """创建数据库和表结构"""
    # 使用相对于 langchain-agent 目录的路径
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    products_dir = os.path.join(base_dir, 'products')
    db_path = os.path.join(products_dir, 'robot_vacuum.db')

    # 确保目录存在
    os.makedirs(products_dir, exist_ok=True)

    # 如果数据库已存在，先删除
    if os.path.exists(db_path):
        print(f"删除现有数据库：{db_path}")
        os.remove(db_path)

    print(f"创建新数据库：{db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 读取 schema 文件并执行
    schema_path = os.path.join(products_dir, 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()

    cursor.executescript(schema_sql)
    print("数据库表结构创建完成")
    return conn, cursor, db_path

def generate_product_data(count=100):
    """生成模拟产品数据"""
    brands = {
        '小米': ['米家扫拖机器人 1S', '米家扫拖机器人 2', '米家扫拖机器人 2 Pro', '米家扫拖机器人 3', '米家扫拖机器人 3C', '米家扫拖机器人 4'],
        '石头': ['石头 G10', '石头 G10S', '石头 G10S Pro', '石头 P10', '石头 P10 Pro', '石头 S8', '石头 S8 Pro Ultra'],
        '科沃斯': ['科沃斯 T10', '科沃斯 T10 Turbo', '科沃斯 T20', '科沃斯 T20 Pro', '科沃斯 X2', '科沃斯 X2 Pro'],
        '云鲸': ['云鲸 J3', '云鲸 J4', '云鲸 J4 Lite', '云鲸 Narwal S10', '云鲸 Narwal S10 Pro'],
        '追觅': ['追觅 S10', '追觅 S10 Pro', '追觅 S10 Plus', '追觅 W10', '追觅 W10 Pro', '追觅 X30'],
        'iRobot': ['Roomba i3', 'Roomba i4', 'Roomba i7', 'Roomba i7+', 'Roomba j7', 'Roomba j7+'],
        '美的': ['美的 M7', '美的 M7 Pro', '美的 M8', '美的 M8 Pro', '美的 W11'],
        '海尔': ['海尔 T600', '海尔 T700', '海尔 T800', '海尔 X1', '海尔 X1 Pro']
    }
    navigation_types = ['LDS 激光导航', '视觉导航', '陀螺仪导航', '激光 + 视觉融合导航', 'dToF 导航']

    products = []
    brand_list = list(brands.keys())

    for i in range(count):
        brand = random.choice(brand_list)
        model = random.choice(brands[brand])

        # 根据品牌设置基准参数
        if brand in ['石头', '科沃斯', '追觅']:
            base_suction = random.randint(2500, 6000)
            base_price = random.randint(2500, 8000)
            base_rating = round(random.uniform(4.0, 5.0), 1)
        elif brand == '小米':
            base_suction = random.randint(2000, 4000)
            base_price = random.randint(1500, 4000)
            base_rating = round(random.uniform(4.2, 4.9), 1)
        elif brand in ['云鲸', 'iRobot']:
            base_suction = random.randint(2000, 5000)
            base_price = random.randint(3000, 7000)
            base_rating = round(random.uniform(4.1, 4.8), 1)
        else:
            base_suction = random.randint(1500, 3500)
            base_price = random.randint(1000, 3000)
            base_rating = round(random.uniform(3.8, 4.6), 1)

        suction_power = base_suction + random.randint(-200, 200)
        price = base_price + random.randint(-300, 300)
        rating = min(5.0, max(1.0, base_rating + random.uniform(-0.2, 0.2)))

        product = {
            'brand': brand,
            'model': model,
            'suction_power': suction_power,
            'navigation_type': random.choice(navigation_types),
            'battery_life': random.choice([90, 120, 150, 180, 210]),
            'dustbin_capacity': round(random.uniform(0.3, 0.8), 1),
            'water_tank_capacity': random.choice([200, 300, 400, 500]),
            'mopping_function': random.choice([True, False]),
            'self_charging': random.choice([True, False]),
            'virtual_wall': random.choice([True, False]),
            'app_control': random.choice([True, False]),
            'voice_control': random.choice([True, False]),
            'noise_level': random.randint(55, 75),
            'cleaning_area': random.choice([80, 100, 120, 150, 180, 200]),
            'price': price,
            'rating': rating,
            'review_count': random.randint(100, 10000),
            'weight': round(random.uniform(3.0, 6.0), 1),
            'height': round(random.uniform(8.0, 12.0), 1),
            'release_year': random.randint(2020, 2024)
        }
        products.append(product)

    return products

def insert_sample_data(cursor, products):
    """插入样本数据到数据库"""
    insert_sql = """
    INSERT INTO products (
        brand, model, suction_power, navigation_type, battery_life,
        dustbin_capacity, water_tank_capacity, mopping_function,
        self_charging, virtual_wall, app_control, voice_control,
        noise_level, cleaning_area, price, rating, review_count,
        weight, height, release_year
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    for product in products:
        cursor.execute(insert_sql, tuple(product.values()))

    print(f"已插入 {len(products)} 条产品数据")

def main():
    """主函数"""
    print("开始初始化扫地机器人推荐系统数据库...")
    conn, cursor, db_path = create_database()

    try:
        print("生成模拟产品数据...")
        products = generate_product_data(100)
        insert_sample_data(cursor, products)
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM products")
        product_count = cursor.fetchone()[0]

        print(f"\n数据库初始化完成！产品表：{product_count} 条记录")
        print(f"数据库文件：{db_path}")

        # 显示示例数据
        print("\n示例产品数据:")
        cursor.execute("""
            SELECT brand, model, suction_power, navigation_type, price, rating
            FROM products ORDER BY RANDOM() LIMIT 5
        """)
        for row in cursor.fetchall():
            print(f"  {row[0]} {row[1]} | 吸力:{row[2]}Pa | 导航:{row[3]} | 价格:{row[4]}元 | 评分:{row[5]}")

    except Exception as e:
        print(f"初始化过程中出现错误：{e}")
        conn.rollback()
    finally:
        conn.close()
        print("\n数据库连接已关闭")

if __name__ == "__main__":
    main()