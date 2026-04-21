#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫地机器人推荐工具 (LangChain Tools)
"""

import re
import json
from typing import Optional, Dict, Any, Union
from langchain_core.tools import StructuredTool
from data.products.recommender import VacuumRobotRecommender


def _parse_user_requirements(input_data: Union[str, Dict]) -> Dict:
    """
    解析用户输入的需求，支持自然语言描述和结构化参数。

    输入可以是：
    1. 自然语言描述："房屋80平米，预算3000元，有宠物，木地板"
    2. JSON字符串：{"house_area": 80, "max_price": 3000}
    3. 字典：{"house_area": 80, "max_price": 3000}

    返回结构化参数字典。
    """
    # 如果已经是字典，直接返回
    if isinstance(input_data, dict):
        return input_data

    # 尝试解析 JSON
    if isinstance(input_data, str):
        input_data = input_data.strip()
        if input_data.startswith("{") and input_data.endswith("}"):
            try:
                return json.loads(input_data)
            except json.JSONDecodeError:
                pass

    # 解析自然语言描述
    params = {}
    text = str(input_data).lower()

    # 解析房屋面积
    area_patterns = [
        r"(\d+)[\s]*平米",
        r"(\d+)[\s]*平方米",
        r"(\d+)[\s]*㎡",
        r"面积[\s:：]*(\d+)",
        r"(\d+)[\s]*平",
    ]
    for pattern in area_patterns:
        match = re.search(pattern, text)
        if match:
            params["house_area"] = int(match.group(1))
            break

    # 解析预算/价格
    price_patterns = [
        r"预算[\s:：]*(\d+)",
        r"价格[\s:：]*(\d+)",
        r"(\d+)[\s]*元",
        r"(\d+)[\s]*块钱",
        r"(\d+)块",
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text)
        if match:
            params["max_price"] = int(match.group(1))
            break

    # 解析品牌
    brands = ["小米", "云鲸", "美的", "石头", "科沃斯", "追觅", "iRobot", "海尔", "格力", "戴森"]
    for brand in brands:
        if brand in text:
            params["brand"] = brand
            break

    # 解析宠物相关
    if "宠物" in text or "猫" in text or "狗" in text or "养宠" in text:
        params["has_pet"] = True
        # 有宠物通常需要更高吸力来清理毛发
        params["min_suction_power"] = 2500

    # 解析地毯相关
    if "地毯" in text:
        params["has_carpet"] = True

    # 解析地板类型
    if "木地板" in text or "木质" in text or "实木" in text:
        params["floor_type"] = "wood"
    elif "瓷砖" in text or "大理石" in text:
        params["floor_type"] = "tile"

    # 解析拖地需求
    if "拖地" in text or "扫拖" in text or "拖布" in text:
        params["need_mopping"] = True

    # 解析导航类型
    if "激光" in text or "lds" in text:
        params["navigation_type"] = "激光"
    elif "视觉" in text:
        params["navigation_type"] = "视觉"

    return params


def _recommend_vacuum_robot_fn(
    brand: Optional[str] = None,
    min_suction_power: int = 1500,
    navigation_type: Optional[str] = None,
    max_price: float = 5000,
    min_battery_life: int = 60,
    house_area: int = 100,
    need_mopping: bool = False,
    need_self_charging: bool = False,
    limit: int = 5,
    # 额外参数（支持自然语言解析）
    input_description: Optional[str] = None,
    has_pet: bool = False,
    has_carpet: bool = False,
    floor_type: Optional[str] = None
) -> str:
    """内部实现函数"""
    try:
        # 如果提供了自然语言描述，先解析
        if input_description:
            parsed = _parse_user_requirements(input_description)
            # 用解析结果覆盖默认参数
            if parsed.get("brand"):
                brand = parsed["brand"]
            if parsed.get("house_area"):
                house_area = parsed["house_area"]
            if parsed.get("max_price"):
                max_price = parsed["max_price"]
            if parsed.get("min_suction_power"):
                min_suction_power = parsed["min_suction_power"]
            if parsed.get("need_mopping"):
                need_mopping = parsed["need_mopping"]
            if parsed.get("navigation_type"):
                navigation_type = parsed["navigation_type"]
            if parsed.get("has_pet"):
                has_pet = parsed["has_pet"]
            if parsed.get("has_carpet"):
                has_carpet = parsed["has_carpet"]

        # 有宠物时提高吸力要求
        if has_pet and min_suction_power < 2500:
            min_suction_power = 2500

        with VacuumRobotRecommender() as recommender:
            results = recommender.recommend_by_criteria(
                brand=brand,
                min_suction=min_suction_power,
                navigation=navigation_type,
                max_price=max_price,
                min_battery=min_battery_life,
                house_area=house_area,
                must_have_mopping=need_mopping,
                must_have_self_charging=need_self_charging,
                limit=limit
            )

            if not results:
                return "很抱歉，没有找到符合您要求的扫地机器人产品。建议您放宽筛选条件，如提高预算、降低吸力要求或考虑其他品牌。"

            output = []
            output.append(f"为您找到 {len(results)} 款符合条件的扫地机器人：")
            output.append("=" * 60)

            for i, item in enumerate(results, 1):
                output.append(f"\n【推荐 #{i}】{item['brand']} {item['model']}")
                output.append("-" * 40)
                output.append(f"  吸力：{item['suction_power']}Pa")
                output.append(f"  导航：{item['navigation_type']}")
                output.append(f"  续航：{item['battery_life']}分钟")
                output.append(f"  适用面积：{item['cleaning_area']}㎡")
                output.append(f"  价格：{item['price']:.0f}元")
                output.append(f"  评分：{item['rating']}/5 ({item['review_count']}条评价)")
                output.append(f"  拖地功能：{item['mopping_function']}")
                output.append(f"  自动回充：{item['self_charging']}")
                output.append(f"  噪音：{item['noise_level']}dB")
                output.append(f"  匹配度：{item['match_score']:.1%}")

            output.append("\n" + "=" * 60)
            output.append("注：以上数据供参考，实际购买请查阅官方最新参数")

            return "\n".join(output)

    except Exception as e:
        return f"推荐系统出现异常：{str(e)}"


from pydantic import BaseModel, Field


class GetVacuumBrandsInput(BaseModel):
    """获取品牌列表的输入"""
    dummy: Optional[str] = Field(default=None, description="此参数不使用，仅用于兼容")


class GetProductCountInput(BaseModel):
    """获取产品总数的输入"""
    dummy: Optional[str] = Field(default=None, description="此参数不使用，仅用于兼容")


def _get_vacuum_brands_fn(dummy: Optional[str] = None) -> str:
    """获取品牌列表"""
    try:
        with VacuumRobotRecommender() as recommender:
            brands = recommender.get_brands()
            if not brands:
                return "当前没有可用的品牌数据"
            return f"可查询的扫地机器人品牌：{', '.join(brands)}"
    except Exception as e:
        return f"获取品牌列表失败：{str(e)}"


def _get_product_count_fn(dummy: Optional[str] = None) -> str:
    """获取产品总数"""
    try:
        with VacuumRobotRecommender() as recommender:
            count = recommender.get_product_count()
            return f"数据库共有 {count} 款扫地机器人产品"
    except Exception as e:
        return f"查询失败：{str(e)}"


def recommend_vacuum_robot_wrapper(input_data: str = "") -> str:
    """
    推荐扫地机器人的包装函数，支持接收字符串或JSON作为输入。

    输入可以是：
    - 自然语言描述："房屋80平米，预算3000元，有宠物，木地板"
    - JSON字符串：{"house_area": 80, "max_price": 3000, "has_pet": true}
    """
    try:
        # 尝试解析为JSON
        if input_data and input_data.strip().startswith("{"):
            try:
                params = json.loads(input_data)
                return _recommend_vacuum_robot_fn(**params)
            except json.JSONDecodeError:
                pass

        # 解析自然语言描述
        params = _parse_user_requirements(input_data)

        # 如果解析出参数，使用解析结果
        if params:
            return _recommend_vacuum_robot_fn(
                brand=params.get("brand"),
                max_price=params.get("max_price", 5000),
                house_area=params.get("house_area", 100),
                min_suction_power=params.get("min_suction_power", 1500),
                need_mopping=params.get("need_mopping", False),
                navigation_type=params.get("navigation_type"),
                has_pet=params.get("has_pet", False),
                limit=5
            )
        else:
            # 没有解析出任何参数，使用默认值
            return _recommend_vacuum_robot_fn()
    except Exception as e:
        return f"推荐系统出现异常：{str(e)}"


class RecommendVacuumRobotInput(BaseModel):
    """推荐扫地机器人的输入"""
    input_description: str = Field(
        default="",
        description="用户需求描述，如：房屋80平米，预算3000元，有宠物，木地板。也可以是JSON格式参数。"
    )


# 使用 StructuredTool 定义工具，明确指定输入 schema
recommend_vacuum_robot = StructuredTool.from_function(
    func=recommend_vacuum_robot_wrapper,
    name="recommend_vacuum_robot",
    description="根据用户需求推荐扫地机器人。输入可以是自然语言描述（如'房屋80平米，预算3000元，有宠物'），也可以是JSON参数。系统会自动解析房屋面积、预算、品牌、宠物情况等条件。",
    args_schema=RecommendVacuumRobotInput,
)

get_vacuum_brands = StructuredTool.from_function(
    func=_get_vacuum_brands_fn,
    name="get_vacuum_brands",
    description="获取数据库中所有可用的扫地机器人品牌列表。不需要任何参数。",
    args_schema=GetVacuumBrandsInput,
)

get_product_count = StructuredTool.from_function(
    func=_get_product_count_fn,
    name="get_product_count",
    description="获取数据库中扫地机器人产品的总数。不需要任何参数。",
    args_schema=GetProductCountInput,
)


# 导出所有工具
vacuum_robot_tools = [
    recommend_vacuum_robot,
    get_vacuum_brands,
    get_product_count
]