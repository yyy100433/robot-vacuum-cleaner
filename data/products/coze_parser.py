#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coze 返回数据解析器：自动解析 Coze 推荐的扫地机器人信息并保存到数据库
"""

import re
import random
import sqlite3
import os
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class CozeRobotParser:
    """Coze 返回的扫地机器人推荐数据解析器"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, 'products', 'robot_vacuum.db')
        self.db_path = db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """确保数据库文件和表结构存在"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
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
            carpet_recognition BOOLEAN,
            auto_mop_lifting BOOLEAN,
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
            suitable_for TEXT,
            source VARCHAR(50) DEFAULT 'coze',
            source_id VARCHAR(100),
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_source ON products(source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_brand_model ON products(brand, model)")

        conn.commit()
        conn.close()

    def parse(self, coze_response: str) -> List[Dict]:
        """解析 Coze 返回的文本，提取扫地机器人产品信息"""
        products = []

        recommend_products = self._parse_recommend_section(coze_response)
        table_products = self._parse_markdown_table(coze_response)
        numbered_products = self._parse_numbered_list(coze_response)
        bullet_products = self._parse_bullet_list(coze_response)
        natural_products = self._parse_natural_language(coze_response)

        products.extend(recommend_products)
        products.extend(table_products)
        products.extend(numbered_products)
        products.extend(bullet_products)
        products.extend(natural_products)

        products = self._deduplicate_products(products)
        return products

    def _parse_recommend_section(self, text: str) -> List[Dict]:
        """解析推荐块格式"""
        products = []
        recommend_pattern = r'(?:#+\s*)?(?:推荐 [一二三四五六七八九十])(?:[：:])\s*([^\n]+)'
        matches = list(re.finditer(recommend_pattern, text, re.MULTILINE))

        for i, match in enumerate(matches):
            title = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end]

            product = self._parse_recommend_block(title, block)
            if product and product.get('brand') and product.get('model'):
                products.append(product)

        return products

    def _parse_recommend_block(self, title: str, block: str) -> Dict:
        """解析单个推荐块"""
        product = {}

        # 提取价格
        price_match = re.search(r'[约]*\s*(\d{4,5})\s*元', title)
        if price_match:
            product['price'] = int(price_match.group(1))

        # 提取品牌和型号
        brand_model = self._extract_brand_model(title)
        if brand_model:
            product['brand'] = brand_model['brand']
            product['model'] = brand_model['model']
        else:
            brand_model = self._extract_brand_model(block)
            if brand_model:
                product['brand'] = brand_model['brand']
                product['model'] = brand_model['model']

        # 从 block 中提取参数
        param_section = re.search(r'(?:核心参数 | 参数)[：:]\s*(.+?)(?=\n\s*(?:缺点 | 注意 | 为什么|$))', block, re.DOTALL)
        if param_section:
            params = param_section.group(1)

            suction_match = re.search(r'(\d+)\s*Pa', params)
            if suction_match:
                product['suction_power'] = int(suction_match.group(1))

            battery_match = re.search(r'(\d+)\s*(?:分钟|min)', params)
            if battery_match:
                product['battery_life'] = int(battery_match.group(1))

            area_match = re.search(r'(\d+)\s*㎡', params)
            if area_match:
                product['cleaning_area'] = int(area_match.group(1))

            if 'LDS' in params or '激光' in params:
                product['navigation_type'] = 'LDS 激光导航'
            elif 'dToF' in params:
                product['navigation_type'] = 'dToF 导航'
            elif '视觉' in params:
                product['navigation_type'] = '视觉导航'

            product['mopping_function'] = '拖' in params or '洗' in params
            product['self_charging'] = '回充' in params or '基站' in params
            product['carpet_recognition'] = '地毯' in params
            product['auto_mop_lifting'] = '抬升' in params

        # 提取 description（为什么匹配你的需求的完整描述）
        desc_patterns = [
            r'为什么匹配你的需求 [：:]\s*(.+?)(?=\n\s*(?:核心参数 | 缺点 | 注意 | 一个需要注意|$))',
            r'为什么适合你 [：:]\s*(.+?)(?=\n\s*(?:核心参数 | 缺点 | 注意|$))',
            r'推荐理由 [：:]\s*(.+?)(?=\n\s*(?:核心参数 | 缺点 | 注意|$))',
        ]
        for pattern in desc_patterns:
            desc_match = re.search(pattern, block, re.DOTALL)
            if desc_match:
                product['description'] = desc_match.group(1).strip()
                break

        # 提取优点/为什么适合
        pros_match = re.search(r'(?:为什么匹配 | 为什么适合 | 推荐理由)[：:]\s*(.+?)(?=\n\s*(?:核心参数 | 缺点 | 注意 | 一个需要注意|$))',
                               block, re.DOTALL)
        if pros_match:
            product['pros'] = pros_match.group(1).strip()

        # 提取缺点
        cons_match = re.search(r'(?:缺点 | 注意)[：:]\s*(.+?)(?=\n\s*(?:核心参数 | 为什么|$))', block, re.DOTALL)
        if cons_match:
            product['cons'] = cons_match.group(1).strip()

        # 生成适用人群/场景
        if product.get('description'):
            desc = product['description']
            suitable_parts = []
            if '木地板' in desc:
                suitable_parts.append('木地板用户')
            if '宠物' in desc or '毛发' in desc:
                suitable_parts.append('养宠家庭')
            if '小户型' in desc or '80' in desc or '90' in desc:
                suitable_parts.append('中小户型')
            if '大户型' in desc or '150' in desc or '200' in desc:
                suitable_parts.append('大户型')
            if '地毯' in desc:
                suitable_parts.append('有地毯的家庭')
            if suitable_parts:
                product['suitable_for'] = '、'.join(suitable_parts)
            else:
                product['suitable_for'] = '家庭日常清洁使用'

        self._set_defaults(product)
        return product

    def _parse_markdown_table(self, text: str) -> List[Dict]:
        """解析 Markdown 表格格式"""
        products = []
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if re.match(r'^\|[-:\s|]+\|\s*$', line) and i > 0:
                header_line = lines[i - 1].strip()
                if header_line.startswith('|') and header_line.endswith('|'):
                    headers = [h.strip() for h in header_line.split('|')[1:-1]]
                    data_lines = []
                    j = i + 1
                    while j < len(lines):
                        data_line = lines[j].strip()
                        if data_line.startswith('|') and data_line.endswith('|') and not re.match(r'^\|[-:\s|]+\|\s*$', data_line):
                            data_lines.append(data_line)
                            j += 1
                        else:
                            break
                    for data_line in data_lines:
                        cells = [cell.strip() for cell in data_line.split('|')[1:-1]]
                        product = self._parse_table_cells(cells, headers)
                        if product and product.get('brand') and product.get('model'):
                            products.append(product)
            i += 1
        return products

    def _parse_table_cells(self, cells: List[str], headers: List[str]) -> Dict:
        """解析表格单元格"""
        if len(cells) < 2:
            return {}
        product = {}
        header_map = {}
        for i, header in enumerate(headers):
            if i < len(cells):
                header_map[header.lower()] = cells[i]

        if '品牌' not in header_map:
            brand_model = self._extract_brand_model(cells[0])
            if brand_model:
                product['brand'] = brand_model['brand']
                if brand_model.get('model') and not brand_model.get('model', '').endswith('_型号'):
                    product['model'] = brand_model['model']

        for i, cell in enumerate(cells):
            header = headers[i].lower() if i < len(headers) else ''
            if '品牌' in header:
                brand_model = self._extract_brand_model(cell)
                if brand_model:
                    product['brand'] = brand_model['brand']
            if '型号' in header or (i == 1 and '品牌' in headers[0].lower()):
                model = cell.strip()
                if model and model.upper() not in ['元', 'PA', '分钟', 'MIN', '㎡', '价格', '吸力', '续航', '特点']:
                    product['model'] = model
            if '价格' in header or re.search(r'\d{4,5}\s*元', cell):
                price_match = re.search(r'(\d{4,5})\s*元', cell)
                if price_match:
                    product['price'] = int(price_match.group(1))
            if '吸力' in header or 'suction' in header:
                suction_match = re.search(r'(\d+)\s*Pa', cell)
                if suction_match:
                    product['suction_power'] = int(suction_match.group(1))
            if '续航' in header or 'battery' in header:
                battery_match = re.search(r'(\d+)\s*(?:分钟|min)', cell)
                if battery_match:
                    product['battery_life'] = int(battery_match.group(1))
            if '面积' in header or '适用面积' in header or '㎡' in cell:
                area_match = re.search(r'(\d+)\s*㎡', cell)
                if area_match:
                    product['cleaning_area'] = int(area_match.group(1))
            if '特点' in header or '功能' in header or '导航' in header:
                if '拖地' in cell or '扫拖' in cell:
                    product['mopping_function'] = True
                if '回充' in cell or '自动充电' in cell:
                    product['self_charging'] = True
                if 'LDS' in cell or '激光' in cell:
                    product['navigation_type'] = 'LDS 激光导航'

        self._set_defaults(product)
        return product

    def _set_defaults(self, product: Dict):
        """设置默认值"""
        if not product.get('cleaning_area'):
            product['cleaning_area'] = self._estimate_area(product.get('price'))
        if not product.get('suction_power'):
            product['suction_power'] = self._estimate_suction(product.get('price'))
        if not product.get('battery_life'):
            product['battery_life'] = self._estimate_battery(product.get('cleaning_area'))
        if not product.get('navigation_type'):
            product['navigation_type'] = 'LDS 激光导航'
        if 'mopping_function' not in product:
            product['mopping_function'] = False
        if 'self_charging' not in product:
            product['self_charging'] = True
        if 'carpet_recognition' not in product:
            product['carpet_recognition'] = product.get('mopping_function', False)
        if 'auto_mop_lifting' not in product:
            product['auto_mop_lifting'] = product.get('carpet_recognition', False)

        # 设置文本字段的默认值
        price = product.get('price', 0)
        brand = product.get('brand', '')
        model = product.get('model', '')

        # description 默认值：根据价格生成推荐理由
        if not product.get('description'):
            if price >= 8000:
                product['description'] = f'{brand} {model} 是一款高端旗舰级扫地机器人，适合大户型和追求高品质清洁体验的用户。配备先进导航系统和强大吸力，能高效完成全屋清洁任务。'
            elif price >= 5000:
                product['description'] = f'{brand} {model} 是中高端扫地机器人，适合注重性价比的家庭。具备扫拖一体功能，能满足日常清洁需求。'
            else:
                product['description'] = f'{brand} {model} 是一款经济实用的扫地机器人，适合中小户型使用。基础清洁功能齐全，性价比高。'

        # pros 默认值：根据价格生成优点描述
        if not product.get('pros'):
            if price >= 8000:
                product['pros'] = '旗舰配置、吸力强劲、导航精准、适合大户型'
            elif price >= 5000:
                product['pros'] = '性能均衡、功能齐全、性价比高'
            else:
                product['pros'] = '价格实惠、基础功能完善、适合入门用户'

        # cons 默认值
        if not product.get('cons'):
            if price >= 8000:
                product['cons'] = '价格较高，适合预算充足的用户'
            elif price >= 5000:
                product['cons'] = '部分高级功能可能不如旗舰型号'
            else:
                product['cons'] = '吸力和续航相对基础，不适合超大户型'

        # suitable_for 默认值
        if not product.get('suitable_for'):
            if price >= 8000:
                product['suitable_for'] = '大户型、高端家庭、追求品质的用户'
            elif price >= 5000:
                product['suitable_for'] = '中等户型家庭、注重性价比的用户'
            else:
                product['suitable_for'] = '中小户型、首次购买扫地机器人的用户'

    def _parse_numbered_list(self, text: str) -> List[Dict]:
        products = []
        pattern = r'(?:^|\n)\s*(?:\d+\.|\(\d+\)|[一二三四五六七八九十]|[①②③④⑤⑥⑦⑧⑨⑩])\s*(.+?)(?=\n\s*(?:\d+\.|\(\d+\)|[一二三四五六七八九十]|[①②③④⑤⑥⑦⑧⑨⑩])|$)'
        items = re.findall(pattern, text, re.DOTALL)
        for item in items:
            product = self._parse_product_block(item)
            if product and product.get('brand') and product.get('model'):
                products.append(product)
        return products

    def _parse_bullet_list(self, text: str) -> List[Dict]:
        products = []
        pattern = r'(?:^|\n)\s*[-*•]\s*(.+?)(?=\n\s*[-*•]|\n\n|$)'
        items = re.findall(pattern, text, re.DOTALL)
        for item in items:
            product = self._parse_product_block(item)
            if product and product.get('brand') and product.get('model'):
                products.append(product)
        return products

    def _parse_product_block(self, block: str) -> Dict:
        product = {}
        block_clean = block.strip()
        first_line = block_clean.split('\n')[0]
        brand_model = self._extract_brand_model(first_line)
        if brand_model:
            product['brand'] = brand_model['brand']
            product['model'] = brand_model['model']

        price_matches = re.findall(r'([¥]?\s*\d{4,5})\s*(?:元 | 块 | 价位)?', block_clean)
        if price_matches:
            for pm in price_matches:
                num = re.sub(r'[^\d]', '', pm)
                if 1000 <= int(num) <= 99999:
                    product['price'] = int(num)
                    break

        suction_match = re.search(r'(\d{3,4})\s*Pa', block_clean)
        if suction_match:
            product['suction_power'] = int(suction_match.group(1))

        battery_match = re.search(r'(\d{2,3})\s*(?:分钟|min|分)', block_clean)
        if battery_match:
            life = int(battery_match.group(1))
            if 60 <= life <= 300:
                product['battery_life'] = life

        area_match = re.search(r'(\d{2,3})\s*㎡', block_clean)
        if area_match:
            product['cleaning_area'] = int(area_match.group(1))

        nav_patterns = {
            'LDS 激光导航': r'LDS|激光',
            '视觉导航': r'视觉',
            'dToF 导航': r'dToF',
        }
        for nav_type, pattern in nav_patterns.items():
            if re.search(pattern, block_clean):
                product['navigation_type'] = nav_type
                break

        product['mopping_function'] = any(kw in block_clean for kw in ['拖地', '扫拖', '水洗', '擦地'])
        product['self_charging'] = any(kw in block_clean for kw in ['回充', '自动充电', '基站'])
        product['carpet_recognition'] = any(kw in block_clean for kw in ['地毯', '抬升'])
        product['auto_mop_lifting'] = '抬升' in block_clean

        pros_patterns = [r'为什么适合.*?[:：]\s*(.+?)(?:\n|$)', r'优点.*?[:：]\s*(.+?)(?:\n|$)']
        for pat in pros_patterns:
            match = re.search(pat, block_clean, re.IGNORECASE)
            if match:
                product['pros'] = match.group(1).strip()
                break

        cons_patterns = [r'注意.*?[:：]\s*(.+?)(?:\n|$)', r'缺点.*?[:：]\s*(.+?)(?:\n|$)']
        for pat in cons_patterns:
            match = re.search(pat, block_clean, re.IGNORECASE)
            if match:
                product['cons'] = match.group(1).strip()
                break

        suitable_patterns = [r'适合.*?[:：]\s*(.+?)(?:\n|$)', r'适用.*?[:：]\s*(.+?)(?:\n|$)']
        for pat in suitable_patterns:
            match = re.search(pat, block_clean, re.IGNORECASE)
            if match:
                product['suitable_for'] = match.group(1).strip()
                break

        product.setdefault('rating', 4.5)
        product.setdefault('review_count', 1000)
        product.setdefault('source', 'coze')
        return product

    def _parse_natural_language(self, text: str) -> List[Dict]:
        products = []
        brands = ['石头', '科沃斯', '小米', '米家', '云鲸', '追觅', '美的', '海尔', 'iRobot', '360', '戴森', '松下']
        for brand in brands:
            pattern = rf'{brand}\s*[A-Za-z\d#]+[^.\n。]{0,100}'
            matches = re.findall(pattern, text)
            for match in matches:
                product = {'brand': brand}
                model_match = re.search(r'[A-Za-z\d#]+', match)
                if model_match:
                    product['model'] = model_match.group(0).strip()
                context_start = max(0, text.find(match) - 50)
                context_end = min(len(text), text.find(match) + len(match) + 50)
                context = text[context_start:context_end]
                price_match = re.search(r'(\d{4,5})\s*元', context)
                if price_match:
                    product['price'] = int(price_match.group(1))
                if product.get('model'):
                    products.append(product)
        return products

    def _extract_brand_model(self, text: str) -> Optional[Dict]:
        """从文本中提取品牌和型号"""
        brands = [
            ('石头', ['石头', 'Roborock']),
            ('科沃斯', ['科沃斯', 'ECOVACS']),
            ('小米', ['小米', 'Xiaomi']),
            ('米家', ['米家', 'MiJia']),
            ('云鲸', ['云鲸', 'Narwal']),
            ('追觅', ['追觅', 'Dreame']),
            ('美的', ['美的', 'Midea']),
            ('海尔', ['海尔', 'Haier']),
            ('iRobot', ['iRobot', 'Roomba']),
            ('360', ['360']),
            ('戴森', ['戴森', 'Dyson']),
            ('松下', ['松下', 'Panasonic']),
        ]

        for brand_cn, brand_keywords in brands:
            for keyword in brand_keywords:
                if keyword in text:
                    pattern = rf'{re.escape(keyword)}\s*([A-Za-z\d]+\s*[A-Za-z\d]*(?:\s+[A-Za-z\d]+)*(?:Pro|Ultra|Plus|Lite|Max|S|E|G|P|\d+)*)'
                    match = re.search(pattern, text)
                    if match:
                        model = match.group(1).strip()
                        if model and len(model) > 0:
                            return {'brand': brand_cn, 'model': model}

                    simple_pattern = rf'{re.escape(keyword)}[\s|]*([A-Z][\d\w\s]*[A-Z]?(?:\s*(?:Pro|Ultra|Plus|Lite|Max))?)'
                    simple_match = re.search(simple_pattern, text, re.IGNORECASE)
                    if simple_match and simple_match.group(1).strip():
                        model = simple_match.group(1).strip()
                        if model.upper() not in ['元', 'PA', '分钟', 'MIN', '㎡']:
                            return {'brand': brand_cn, 'model': model}

                    alnum_pattern = rf'{re.escape(keyword)}[\s|]*([A-Z]\d+[A-Z]?)'
                    alnum_match = re.search(alnum_pattern, text, re.IGNORECASE)
                    if alnum_match:
                        return {'brand': brand_cn, 'model': alnum_match.group(1).strip()}

                    return {'brand': brand_cn, 'model': f'{brand_cn}_型号'}
        return None

    def _estimate_suction(self, price: Optional[int]) -> int:
        if not price:
            return 3000
        if price < 2000:
            return random.randint(2000, 3000)
        elif price < 3500:
            return random.randint(3000, 5000)
        elif price < 5000:
            return random.randint(5000, 7000)
        else:
            return random.randint(7000, 8000)

    def _estimate_battery(self, area: Optional[int]) -> int:
        if not area:
            return 150
        if area < 80:
            return random.randint(90, 120)
        elif area < 120:
            return random.randint(120, 150)
        else:
            return random.randint(150, 210)

    def _estimate_area(self, price: Optional[int]) -> int:
        if not price:
            return 120
        if price < 2000:
            return random.randint(80, 100)
        elif price < 3500:
            return random.randint(100, 120)
        elif price < 5000:
            return random.randint(120, 150)
        else:
            return random.randint(150, 200)

    def _deduplicate_products(self, products: List[Dict]) -> List[Dict]:
        seen = set()
        unique = []
        for p in products:
            key = (p.get('brand', ''), p.get('model', ''))
            if key[0] and key[1] and key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    def save_to_database(self, products: List[Dict], source_id: str = None) -> Tuple[int, int, int]:
        if not products:
            return 0, 0, 0

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            inserted = 0
            skipped = 0
            errors = 0

            for product in products:
                try:
                    # 先设置默认值，确保所有必要字段都有值
                    self._set_defaults(product)

                    cursor.execute(
                        "SELECT id FROM products WHERE brand = ? AND model = ?",
                        (product.get('brand'), product.get('model'))
                    )
                    if cursor.fetchone():
                        skipped += 1
                        continue

                    cursor.execute("""
                        INSERT INTO products (
                            brand, model, price, suction_power, battery_life, cleaning_area,
                            navigation_type, mopping_function, self_charging, carpet_recognition,
                            auto_mop_lifting, dustbin_capacity, water_tank_capacity,
                            virtual_wall, app_control, voice_control, noise_level, weight, height,
                            release_year, rating, review_count, description, pros, cons,
                            suitable_for, source, source_id, is_active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        product.get('brand'),
                        product.get('model'),
                        product.get('price'),
                        product.get('suction_power'),
                        product.get('battery_life'),
                        product.get('cleaning_area'),
                        product.get('navigation_type'),
                        1 if product.get('mopping_function') else 0,
                        1 if product.get('self_charging') else 0,
                        1 if product.get('carpet_recognition') else 0,
                        1 if product.get('auto_mop_lifting') else 0,
                        product.get('dustbin_capacity', 0.3),
                        product.get('water_tank_capacity', 200),
                        1, 1, 1,
                        product.get('noise_level', 65),
                        product.get('weight', 4.0),
                        product.get('height', 11.0),
                        product.get('release_year', 2024),
                        product.get('rating', 4.5),
                        product.get('review_count', 1000),
                        product.get('description', ''),
                        product.get('pros', ''),
                        product.get('cons', ''),
                        product.get('suitable_for', ''),
                        'coze',
                        source_id,
                        1
                    ))

                    print(f"  已添加：{product.get('brand')} {product.get('model')}")
                    inserted += 1

                except Exception as e:
                    print(f"  错误：{product.get('brand')} {product.get('model')} - {e}")
                    errors += 1

            conn.commit()
            return inserted, skipped, errors

        except Exception as e:
            print(f"数据库操作错误：{e}")
            if conn:
                conn.rollback()
            return 0, 0, len(products)
        finally:
            if conn:
                conn.close()


def parse_and_save(coze_response: str, db_path: str = None, source_id: str = None) -> Tuple[int, int, int]:
    parser = CozeRobotParser(db_path)
    products = parser.parse(coze_response)

    print(f"\n=== 解析结果 ===")
    print(f"共解析出 {len(products)} 款产品")

    for p in products:
        print(f"  - {p.get('brand')} {p.get('model')} (价格:{p.get('price')}元)")
        if p.get('description'):
            print(f"    description: {p.get('description')[:50]}...")
        if p.get('suitable_for'):
            print(f"    suitable_for: {p.get('suitable_for')}")

    inserted, skipped, errors = parser.save_to_database(products, source_id)

    print(f"\n=== 保存结果 ===")
    print(f"已插入：{inserted} 款")
    print(f"已跳过：{skipped} 款")
    print(f"错误数：{errors} 款")

    return inserted, skipped, errors


if __name__ == "__main__":
    test_response = """
    根据您的预算和需求，我为您推荐以下几款扫地机器人：

    ## 推荐一：科沃斯 X2 Pro（约 6999 元）
    为什么匹配你的需求：配备全软胶主刷，贴合木地板纹理清洁同时避免刮伤漆面
    核心参数：吸力 7000Pa，续航 180 分钟，适用面积 150 ㎡
    缺点：价格较高

    ## 推荐二：石头 P10 Pro（约 3999 元）
    为什么适合你：具备地毯识别自动抬升拖布功能
    核心参数：吸力 6000Pa，续航 160 分钟
    """

    print("测试解析...")
    parse_and_save(test_response)
