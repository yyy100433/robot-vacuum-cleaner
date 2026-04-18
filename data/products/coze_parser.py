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
        # 确保数据库和表结构存在
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """确保数据库文件和表结构存在"""
        # 创建目录
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        # 初始化数据库表结构（如果不存在）
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

        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_source ON products(source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_brand_model ON products(brand, model)")

        conn.commit()
        conn.close()
        print(f"[DEBUG] 数据库已初始化: {self.db_path}")

    def parse(self, coze_response: str) -> List[Dict]:
        """解析 Coze 返回的文本，提取扫地机器人产品信息"""
        print(f"[DEBUG] 开始解析 Coze 响应，长度：{len(coze_response)}")

        # 优先打印前500字符用于调试
        print(f"[DEBUG] 响应预览：{coze_response[:500]}")

        products = []

        # ✅ 新增：解析 "## 推荐X：" 格式（Coze 常用格式）
        recommend_products = self._parse_recommend_section(coze_response)
        print(f"[DEBUG] 推荐章节解析：{len(recommend_products)} 款")
        products.extend(recommend_products)

        table_products = self._parse_markdown_table(coze_response)
        print(f"[DEBUG] Markdown 表格解析：{len(table_products)} 款")
        products.extend(table_products)

        numbered_products = self._parse_numbered_list(coze_response)
        print(f"[DEBUG] 编号列表解析：{len(numbered_products)} 款")
        products.extend(numbered_products)

        bullet_products = self._parse_bullet_list(coze_response)
        print(f"[DEBUG] 项目符号解析：{len(bullet_products)} 款")
        products.extend(bullet_products)

        natural_products = self._parse_natural_language(coze_response)
        print(f"[DEBUG] 自然语言解析：{len(natural_products)} 款")
        products.extend(natural_products)

        before_dedup = len(products)
        products = self._deduplicate_products(products)
        print(f"[DEBUG] 去重前 {before_dedup} 款，去重后 {len(products)} 款")

        return products

    def _parse_recommend_section(self, text: str) -> List[Dict]:
        """
        解析 "## 推荐一：XXX" 或 "### 推荐一 XXX" 格式的产品推荐
        Coze 常用的推荐格式
        """
        products = []

        # 匹配推荐块 - 支持多种格式
        # 格式1: ## 推荐一：科沃斯X2 Pro（约6999元）
        # 格式2: ### 推荐一 科沃斯X2 Pro
        # 格式3: **推荐一：科沃斯X2 Pro**

        # 按推荐块分割文本
        # 匹配 "推荐一："、"推荐二：" 等标记
        recommend_pattern = r'(?:#+\s*)?(?:推荐[一二三四五六七八九十])(?:[：:])\s*([^\n]+)'

        # 找到所有推荐标题的位置
        matches = list(re.finditer(recommend_pattern, text, re.MULTILINE))

        for i, match in enumerate(matches):
            # 获取推荐标题（产品名和价格）
            title = match.group(1).strip()

            # 确定这个推荐块的内容范围
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end]

            # 解析这个产品块
            product = self._parse_recommend_block(title, block)
            if product and product.get('brand') and product.get('model'):
                products.append(product)
                print(
                    f"[DEBUG] 解析到产品: {product.get('brand')} {product.get('model')} - ¥{product.get('price', '未知')}")

        return products

    def _parse_recommend_block(self, title: str, block: str) -> Dict:
        """解析单个推荐块"""
        product = {}

        # 1. 从标题提取产品和价格
        # 格式: "科沃斯X2 Pro（约6999元）" 或 "科沃斯 X2 Pro 6999元"

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
            # 如果品牌提取失败，尝试从内容中提取
            brand_model = self._extract_brand_model(block)
            if brand_model:
                product['brand'] = brand_model['brand']
                product['model'] = brand_model['model']

        # 2. 从 block 中提取参数
        # 查找 "核心参数" 行
        param_section = re.search(r'(?:核心参数|参数)[：:]\s*(.+?)(?=\n\s*(?:缺点|注意|为什么|$))', block, re.DOTALL)
        if param_section:
            params = param_section.group(1)

            # 提取吸力
            suction_match = re.search(r'(\d+)\s*Pa', params)
            if suction_match:
                product['suction_power'] = int(suction_match.group(1))

            # 提取续航
            battery_match = re.search(r'(\d+)\s*(?:分钟|min)', params)
            if battery_match:
                product['battery_life'] = int(battery_match.group(1))

            # 提取适用面积
            area_match = re.search(r'(\d+)\s*㎡', params)
            if area_match:
                product['cleaning_area'] = int(area_match.group(1))

            # 提取导航类型
            if 'LDS' in params or '激光' in params:
                product['navigation_type'] = 'LDS 激光导航'
            elif 'dToF' in params:
                product['navigation_type'] = 'dToF 导航'
            elif '视觉' in params:
                product['navigation_type'] = '视觉导航'

            # 功能判断
            product['mopping_function'] = '拖' in params or '洗' in params
            product['self_charging'] = '回充' in params or '基站' in params
            product['carpet_recognition'] = '地毯' in params
            product['auto_mop_lifting'] = '抬升' in params

        # 3. 提取优点/为什么适合
        pros_match = re.search(r'(?:为什么匹配|为什么适合|推荐理由)[：:]\s*(.+?)(?=\n\s*(?:核心参数|缺点|注意|$))',
                               block, re.DOTALL)
        if pros_match:
            product['pros'] = pros_match.group(1).strip()

        # 4. 提取缺点
        cons_match = re.search(r'(?:缺点|注意)[：:]\s*(.+?)(?=\n\s*(?:核心参数|为什么|$))', block, re.DOTALL)
        if cons_match:
            product['cons'] = cons_match.group(1).strip()

        # 5. 设置默认值
        self._set_defaults(product)

        return product

    def _parse_markdown_table(self, text: str) -> List[Dict]:
        """解析 Markdown 表格格式的产品推荐"""
        products = []

        # 按行处理，找到表格区域
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # 检查是否是分隔行（如 |------|------|）
            # 注意：- 必须放在字符组的开头或结尾，否则会被解释为范围操作符
            if re.match(r'^\|[-:\s|]+\|\s*$', line) and i > 0:
                # 获取表头行
                header_line = lines[i - 1].strip()
                if header_line.startswith('|') and header_line.endswith('|'):
                    # 解析表头
                    headers = [h.strip() for h in header_line.split('|')[1:-1]]

                    # 收集数据行
                    data_lines = []
                    j = i + 1
                    while j < len(lines):
                        data_line = lines[j].strip()
                        # 检查是否是数据行（以 | 开头和结尾，且不是分隔行）
                        if data_line.startswith('|') and data_line.endswith('|') and not re.match(r'^\|[-:\s|]+\|\s*$',
                                                                                                  data_line):
                            data_lines.append(data_line)
                            j += 1
                        else:
                            break

                    # 解析每一行数据
                    for data_line in data_lines:
                        cells = [cell.strip() for cell in data_line.split('|')[1:-1]]
                        product = self._parse_table_cells(cells, headers)
                        if product and product.get('brand') and product.get('model'):
                            products.append(product)
            i += 1

        return products

    def _parse_table_cells(self, cells: List[str], headers: List[str]) -> Dict:
        """根据单元格列表和表头解析产品信息"""
        if len(cells) < 2:
            return {}

        product = {}

        # 首先检查是否有表头映射
        header_map = {}
        for i, header in enumerate(headers):
            if i < len(cells):
                header_map[header.lower()] = cells[i]

        # 从第一列提取品牌（如果没有明确的品牌列）
        if '品牌' not in header_map:
            brand_model = self._extract_brand_model(cells[0])
            if brand_model:
                product['brand'] = brand_model['brand']
                # 如果 extract_brand_model 返回了有效型号，使用它
                if brand_model.get('model') and not brand_model.get('model', '').endswith('_型号'):
                    product['model'] = brand_model['model']

        # 处理每个单元格
        for i, cell in enumerate(cells):
            header = headers[i].lower() if i < len(headers) else ''

            # 品牌列
            if '品牌' in header:
                brand_model = self._extract_brand_model(cell)
                if brand_model:
                    product['brand'] = brand_model['brand']

            # 型号列 - 直接从单元格内容提取型号
            if '型号' in header or (i == 1 and '品牌' in headers[0].lower()):
                model = cell.strip()
                # 确保型号不是明显的非型号词
                if model and model.upper() not in ['元', 'PA', '分钟', 'MIN', '㎡', '价格', '吸力', '续航', '特点']:
                    product['model'] = model

            # 价格列
            if '价格' in header or re.search(r'\d{4,5}\s*元', cell):
                price_match = re.search(r'(\d{4,5})\s*元', cell)
                if price_match:
                    product['price'] = int(price_match.group(1))

            # 吸力列
            if '吸力' in header or 'suction' in header:
                suction_match = re.search(r'(\d+)\s*Pa', cell)
                if suction_match:
                    product['suction_power'] = int(suction_match.group(1))

            # 续航列
            if '续航' in header or 'battery' in header:
                battery_match = re.search(r'(\d+)\s*(?:分钟|min)', cell)
                if battery_match:
                    product['battery_life'] = int(battery_match.group(1))

            # 面积列
            if '面积' in header or '适用面积' in header or '㎡' in cell:
                area_match = re.search(r'(\d+)\s*㎡', cell)
                if area_match:
                    product['cleaning_area'] = int(area_match.group(1))

            # 特点/功能列
            if '特点' in header or '功能' in header or '导航' in header:
                if '拖地' in cell or '扫拖' in cell:
                    product['mopping_function'] = True
                if '回充' in cell or '自动充电' in cell:
                    product['self_charging'] = True
                if 'LDS' in cell or '激光' in cell:
                    product['navigation_type'] = 'LDS 激光导航'
                if 'dToF' in cell:
                    product['navigation_type'] = 'dToF 导航'
                if '视觉' in cell:
                    product['navigation_type'] = '视觉导航'

        # 确保必要字段有默认值
        self._set_defaults(product)

        return product

    def _set_defaults(self, product: Dict):
        """为产品设置默认值，确保数据库插入时不会失败"""
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
            '激光 + 视觉融合导航': r'融合 | 双模',
        }
        for nav_type, pattern in nav_patterns.items():
            if re.search(pattern, block_clean):
                product['navigation_type'] = nav_type
                break

        product['mopping_function'] = any(kw in block_clean for kw in ['拖地', '扫拖', '水洗', '擦地'])
        product['self_charging'] = any(kw in block_clean for kw in ['回充', '自动充电', '基站'])
        product['carpet_recognition'] = any(kw in block_clean for kw in ['地毯', '抬升'])
        product['auto_mop_lifting'] = '抬升' in block_clean

        pros_patterns = [r'为什么适合.*?[:：]\s*(.+?)(?:\n|$)', r'优点.*?[:：]\s*(.+?)(?:\n|$)',
                         r'亮点.*?[:：]\s*(.+?)(?:\n|$)']
        for pat in pros_patterns:
            match = re.search(pat, block_clean, re.IGNORECASE)
            if match:
                product['pros'] = match.group(1).strip()
                break

        cons_patterns = [r'注意.*?[:：]\s*(.+?)(?:\n|$)', r'缺点.*?[:：]\s*(.+?)(?:\n|$)', r'不足.*?[:：]\s*(.+?)(?:\n|$)']
        for pat in cons_patterns:
            match = re.search(pat, block_clean, re.IGNORECASE)
            if match:
                product['cons'] = match.group(1).strip()
                break

        if not product.get('pros'):
            desc_match = re.search(r'(?:推荐|#\s*)[^.\n]{20,100}', block_clean)
            if desc_match:
                product['description'] = desc_match.group(0).strip()

        suitable_patterns = [r'适合.*?[:：]\s*(.+?)(?:\n|$)', r'适用.*?[:：]\s*(.+?)(?:\n|$)']
        for pat in suitable_patterns:
            match = re.search(pat, block_clean, re.IGNORECASE)
            if match:
                product['suitable_for'] = match.group(1).strip()
                break

        product.setdefault('mopping_function', '拖' in block_clean or '洗' in block_clean)
        product.setdefault('self_charging', '基' in block_clean or '充' in block_clean)
        product.setdefault('suction_power', self._estimate_suction(product.get('price')))
        product.setdefault('battery_life', self._estimate_battery(product.get('cleaning_area')))
        product.setdefault('cleaning_area', self._estimate_area(product.get('price')))
        product.setdefault('carpet_recognition', product.get('mopping_function', False))
        product.setdefault('auto_mop_lifting', product.get('carpet_recognition', False))
        product.setdefault('rating', 4.5)
        product.setdefault('review_count', 1000)
        product.setdefault('source', 'coze')

        return product

    def _parse_natural_language(self, text: str) -> List[Dict]:
        products = []
        brands = ['石头', '科沃斯', '小米', '米家', '云鲸', '追觅', '美的', '海尔', 'iRobot', '360', '戴森', '松下']
        for brand in brands:
            pattern = rf'{brand}\s*[A-Za-z\d#]+[^.\n。]{0, 100}'
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
                    # 首先尝试匹配品牌后面的英文和数字（型号通常包含英文和数字）
                    # 改进：允许型号中包含更多字符，包括空格
                    pattern = rf'{re.escape(keyword)}\s*([A-Za-z\d]+\s*[A-Za-z\d]*(?:\s+[A-Za-z\d]+)*(?:Pro|Ultra|Plus|Lite|Max|S|E|G|P|\d+)*)'
                    match = re.search(pattern, text)
                    if match:
                        model = match.group(1).strip()
                        if model and len(model) > 0:
                            return {'brand': brand_cn, 'model': model}

                    # 尝试匹配简单的字母 + 数字型号（如 G20S, X2 Pro, J4 等）
                    simple_pattern = rf'{re.escape(keyword)}[\s|]*([A-Z][\d\w\s]*[A-Z]?(?:\s*(?:Pro|Ultra|Plus|Lite|Max))?)'
                    simple_match = re.search(simple_pattern, text, re.IGNORECASE)
                    if simple_match and simple_match.group(1).strip():
                        model = simple_match.group(1).strip()
                        # 确保型号不是明显的非型号词
                        if model.upper() not in ['元', 'PA', '分钟', 'MIN', '㎡']:
                            return {'brand': brand_cn, 'model': model}

                    # 尝试匹配纯字母数字组合（如 J4, X2 等）
                    alnum_pattern = rf'{re.escape(keyword)}[\s|]*([A-Z]\d+[A-Z]?)'
                    alnum_match = re.search(alnum_pattern, text, re.IGNORECASE)
                    if alnum_match:
                        return {'brand': brand_cn, 'model': alnum_match.group(1).strip()}

                    # 最后手段：如果确实找不到型号，返回品牌 + 占位符
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
                    cursor.execute(
                        "SELECT id FROM products WHERE brand = ? AND model = ?",
                        (product.get('brand'), product.get('model'))
                    )
                    if cursor.fetchone():
                        print(f"  跳过重复：{product.get('brand')} {product.get('model')}")
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
                        product.get('navigation_type') or 'LDS 激光导航',
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
                        product.get('description'),
                        product.get('pros'),
                        product.get('cons'),
                        product.get('suitable_for'),
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

    inserted, skipped, errors = parser.save_to_database(products, source_id)

    print(f"\n=== 保存结果 ===")
    print(f"已插入：{inserted} 款")
    print(f"已跳过：{skipped} 款")
    print(f"错误数：{errors} 款")

    return inserted, skipped, errors


if __name__ == "__main__":
    test_response = """
    根据您的预算和需求，我为您推荐以下几款扫地机器人：

    1. **石头 P10 Pro** - 3999 元
       - 吸力：7000Pa
       - 续航：180 分钟
       - 适用面积：150 ㎡
       - LDS 激光导航，支持拖地和自动回充
       - 为什么适合您：配备全软胶主刷，贴合木地板纹理清洁同时避免刮伤漆面

    2. **科沃斯 T20 Pro** - 3599 元
       - 吸力：6000Pa
       - 续航：160 分钟
       - dToF 导航，热水洗拖布
       - 为什么适合您：具备地毯识别自动抬升拖布功能

    3. **云鲸 J4** - 4299 元
       - 吸力：7800Pa
       - 气旋导流式零缠绕滚刷
       - 适合养宠家庭
    """

    print("测试解析...")
    parse_and_save(test_response)