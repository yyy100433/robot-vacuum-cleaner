#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫地机器人推荐工具 (LangChain Tools)
"""

from typing import Optional, Dict, Any
from langchain_core.tools import StructuredTool
from data.products.recommender import VacuumRobotRecommender


def _recommend_vacuum_robot_fn(
    brand: Optional[str] = None,
    min_suction_power: int = 1500,
    navigation_type: Optional[str] = None,
    max_price: float = 5000,
    min_battery_life: int = 60,
    house_area: int = 100,
    need_mopping: bool = False,
    need_self_charging: bool = False,
    limit: int = 5
) -> str:
    """内部实现函数"""
    try:
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


# 使用 StructuredTool 定义工具，明确指定输入 schema
recommend_vacuum_robot = StructuredTool.from_function(
    func=_recommend_vacuum_robot_fn,
    name="recommend_vacuum_robot",
    description="根据用户需求推荐扫地机器人。可以指定品牌、吸力、导航类型、价格、续航、房屋面积等条件。",
    infer_schema=True,
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