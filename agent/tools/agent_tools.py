import os.path
import csv
from datetime import datetime
import json
from typing import Dict, Optional
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from langchain_core.tools import tool
from rag.rag_service import RagSummarizeService
from utils.config_handler import agent_conf
from utils.path_tool import get_abs_path
from utils.logger_handler import logger

# 导入扫地机器人推荐工具
from data.products.tools import recommend_vacuum_robot, get_vacuum_brands, get_product_count

rag = RagSummarizeService()
external_data: Dict[str, Dict[str, dict]] = {}

# 简单的上下文存储，用于在工具间共享会话信息
_session_context: Dict[str, any] = {}


def _request_json(base_url: str, params: dict) -> dict:
    """发起一个简单的 GET 请求并解析 JSON。"""
    url = f"{base_url}?{urlencode(params)}"
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _format_record(user_id: str, month: str, record: dict) -> str:
    """把结构化用户记录转成更适合模型消费的纯文本。"""
    parts = [f"用户ID: {user_id}", f"月份: {month}"]
    for key in ("特征", "效率", "耗材", "对比"):
        value = (record.get(key) or "").strip()
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


@tool
def rag_summarize(query: str, chat_history: str = ""):
    """从本地知识库中检索与扫地机器人相关的参考资料并总结返回。用于回答产品使用、故障排查等问题。"""
    return rag.rag_summarize(query, chat_history)


@tool
def get_weather(city: str):
    """获取指定城市的实时天气信息，返回温度、体感温度、降水、风速等数据。"""
    city = city.strip()
    if not city:
        return "城市不能为空。"

    try:
        geocode_data = _request_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": city, "count": 1, "language": "zh", "format": "json"},
        )
        results = geocode_data.get("results") or []
        if not results:
            return f"未查询到城市 {city} 的地理信息，请确认城市名称。"

        location = results[0]
        latitude = location["latitude"]
        longitude = location["longitude"]
        resolved_name = location.get("name", city)
        admin1 = location.get("admin1", "")
        country = location.get("country", "")

        weather_data = _request_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": ",".join(
                    [
                        "temperature_2m",
                        "apparent_temperature",
                        "relative_humidity_2m",
                        "precipitation",
                        "wind_speed_10m",
                        "weather_code",
                    ]
                ),
                "timezone": "auto",
            },
        )
        current = weather_data.get("current") or {}
        if not current:
            return f"已定位到 {resolved_name}，但未获取到实时天气数据。"

        weather_code_map = {
            0: "晴",
            1: "大部晴朗",
            2: "局部多云",
            3: "阴",
            45: "雾",
            48: "冻雾",
            51: "小毛毛雨",
            53: "毛毛雨",
            55: "强毛毛雨",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            80: "阵雨",
            81: "较强阵雨",
            82: "强阵雨",
            95: "雷暴",
        }
        weather_text = weather_code_map.get(current.get("weather_code"), "未知天气")
        location_text = ", ".join(filter(None, [resolved_name, admin1, country]))
        return (
            f"{location_text} 当前天气：{weather_text}；"
            f"温度 {current.get('temperature_2m')}°C，"
            f"体感 {current.get('apparent_temperature')}°C，"
            f"相对湿度 {current.get('relative_humidity_2m')}%，"
            f"降水 {current.get('precipitation')} mm，"
            f"风速 {current.get('wind_speed_10m')} km/h。"
        )
    except URLError as e:
        logger.warning(f"天气查询失败: {str(e)}")
        return f"天气服务当前不可用，无法获取 {city} 的实时天气。"
    except Exception as e:
        logger.error(f"天气查询异常: {str(e)}", exc_info=True)
        return f"获取 {city} 天气时发生异常。"


@tool
def get_user_location(dummy: str = ""):
    """获取当前会话绑定的城市名称。未绑定时返回未知，不允许编造。"""
    # 优先从工具调用上下文中获取（如果有）
    context_city = _session_context.get("extracted_city", "").strip()
    if context_city:
        return context_city

    city = os.getenv("AGENT_USER_CITY", "").strip()
    if city:
        return city
    return "当前会话未绑定城市信息，请让用户明确提供所在城市。"


@tool
def get_user_id(dummy: str = ""):
    """获取当前会话绑定的用户ID。未绑定时返回未知，不允许随机生成。"""
    generate_external_data()

    # 优先从工具调用上下文中获取（如果有）
    context_user_id = _session_context.get("extracted_user_id", "").strip()
    if context_user_id and context_user_id in external_data:
        return context_user_id

    user_id = os.getenv("AGENT_USER_ID", "").strip()
    if user_id and user_id in external_data:
        return user_id
    return "当前会话未绑定用户ID，请让用户明确提供用户ID。"


@tool
def get_current_month(dummy: str = ""):
    """获取当前月份，格式为 YYYY-MM。"""
    return datetime.now().strftime("%Y-%m")


def generate_external_data():
    """懒加载外部用户记录，只在首次需要时读取 CSV。"""
    if external_data:
        return
    external_data_path = get_abs_path(agent_conf["external_data_path"])
    if not os.path.exists(external_data_path):
        raise FileNotFoundError(f"外部数据文件{external_data_path}不存在")

    with open(external_data_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            user_id = (row.get("用户ID") or "").strip()
            month = (row.get("时间") or "").strip()
            if not user_id or not month:
                continue

            if user_id not in external_data:
                external_data[user_id] = {}

            external_data[user_id][month] = {
                "特征": (row.get("特征") or "").strip(),
                "效率": (row.get("清洁效率") or "").strip(),
                "耗材": (row.get("耗材") or "").strip(),
                "对比": (row.get("对比") or "").strip(),
            }


@tool
def list_report_months(user_id: str):
    """列出指定用户有哪些可查询的报告月份。"""
    generate_external_data()
    months = sorted(external_data.get(user_id, {}).keys())
    if not months:
        return f"未找到用户 {user_id} 的可用报告月份。"
    return f"用户 {user_id} 可查询月份：{', '.join(months)}"


@tool
def fetch_latest_external_data(user_id: str):
    """获取指定用户最近一个月的使用记录。"""
    generate_external_data()
    if user_id not in external_data:
        return f"未找到用户 {user_id} 的使用数据。"

    latest_month = sorted(external_data[user_id].keys())[-1]
    return _format_record(user_id, latest_month, external_data[user_id][latest_month])


@tool
def get_user_profile(user_id: str):
    """获取指定用户的基础画像和最近记录摘要。"""
    generate_external_data()
    user_records = external_data.get(user_id)
    if not user_records:
        return f"未找到用户 {user_id} 的画像信息。"

    months = sorted(user_records.keys())
    latest_month = months[-1]
    latest_record = user_records[latest_month]
    feature = latest_record.get("特征") or "未知"
    return (
        f"用户 {user_id} 的基础画像：{feature}。\n"
        f"可查询月份：{', '.join(months)}。\n"
        f"最近一期记录摘要：\n{_format_record(user_id, latest_month, latest_record)}"
    )


@tool
def fetch_external_data(user_id: str, month: str = ""):
    """获取指定用户在指定月份的使用记录。可以传入 user_id 和 month 作为单独参数，也可以传入 JSON 格式如 {"user_id": "1001", "month": "2025-12"}。"""
    generate_external_data()

    # 尝试解析 JSON 格式的输入
    try:
        if user_id.startswith("{") and user_id.endswith("}"):
            import json
            data = json.loads(user_id)
            user_id = data.get("user_id", "").strip()
            month = data.get("month", "").strip()
    except (json.JSONDecodeError, AttributeError):
        pass

    if not user_id:
        return "用户ID不能为空。"

    if user_id not in external_data:
        logger.warning(f"未能检索到用户{user_id}的使用数据")
        return "未检索到该用户的使用数据。"

    # 如果未指定月份，使用最新月份
    if not month:
        available_months = sorted(external_data[user_id].keys())
        if available_months:
            month = available_months[-1]

    try:
        return _format_record(user_id, month, external_data[user_id][month])
    except KeyError:
        available_months = sorted(external_data[user_id].keys())
        if available_months:
            latest_month = available_months[-1]
            logger.warning(
                f"未能检索到用户{user_id}在{month}的使用数据，降级为最近月份{latest_month}"
            )
            return (
                f"未检索到 {month} 数据，以下为最近月份 {latest_month} 的数据：\n"
                f"{_format_record(user_id, latest_month, external_data[user_id][latest_month])}"
            )

        logger.warning(f"用户{user_id}暂无任何月份使用数据")
        return "该用户暂无可用使用数据。"

def set_session_context(city: Optional[str] = None, user_id: Optional[str] = None, chat_history: str = ""):
    """设置工具调用的会话上下文，让工具能获取历史信息。"""
    if city:
        _session_context["extracted_city"] = city
    if user_id:
        _session_context["extracted_user_id"] = user_id
    if chat_history:
        _session_context["chat_history"] = chat_history


def clear_session_context():
    """清理会话上下文。"""
    _session_context.clear()


@tool
def fill_context_for_report(dummy: str = ""):
    """为报告生成场景注入上下文标记，仅在生成个人使用报告前调用。"""
    return "fill_context_for_report已调用"
# 导出所有工具，包括扫地机器人推荐工具
__all__ = [
    'rag_summarize', 'get_weather', 'get_user_location', 'get_user_id',
    'get_current_month', 'list_report_months', 'fetch_latest_external_data',
    'get_user_profile', 'fetch_external_data', 'fill_context_for_report',
    'set_session_context', 'clear_session_context',
    'recommend_vacuum_robot', 'get_vacuum_brands', 'get_product_count',
]
