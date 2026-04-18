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


def _save_user_to_csv(user_id: str, month: str, record: dict):
    """将新用户记录追加保存到CSV文件中，实现数据持久化。"""
    external_data_path = get_abs_path(agent_conf["external_data_path"])

    with open(external_data_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["用户ID", "特征", "清洁效率", "耗材", "对比", "时间"])
        # 如果文件为空或只有表头，先写入表头
        if f.tell() == 0:
            writer.writeheader()
        writer.writerow({
            "用户ID": user_id,
            "特征": record.get("特征", ""),
            "清洁效率": record.get("效率", ""),
            "耗材": record.get("耗材", ""),
            "对比": record.get("对比", ""),
            "时间": month
        })
    logger.info(f"新用户 {user_id} 的记录已持久化到CSV文件")


@tool
def create_user_report(input_data: str):
    """为新用户创建初始报告数据。当用户ID不存在时，自动创建该用户的基础记录。

    参数格式：
    JSON字符串：{"user_id": "1011", "profile": "80㎡公寓 | 单身 | 木地板", "month": "2026-04"}
    month 可省略，默认使用当前月份；profile 可省略，默认使用通用画像。

    返回:
        创建成功的提示信息或错误信息
    """
    generate_external_data()

    # 解析输入
    user_id = ""
    profile = ""
    month = ""

    try:
        if input_data.startswith("{") and input_data.endswith("}"):
            data = json.loads(input_data)
            user_id = data.get("user_id", "").strip()
            profile = data.get("profile", "").strip()
            month = data.get("month", "").strip()
        else:
            # 如果不是JSON，当作user_id处理
            user_id = input_data.strip()
    except json.JSONDecodeError:
        return "JSON解析失败，请使用正确格式：{\"user_id\": \"1011\", \"profile\": \"用户画像\"}"

    if not user_id:
        return "用户ID不能为空。"

    user_id = user_id.strip()

    # 验证用户ID格式（应该是数字）
    if not user_id.isdigit():
        return "用户ID必须是纯数字格式，如 '1011'。"

    # 检查用户是否已存在
    if user_id in external_data:
        return f"用户 {user_id} 已存在，无需创建。现有月份：{', '.join(sorted(external_data[user_id].keys()))}"

    # 使用当前月份作为默认值
    if not month:
        month = datetime.now().strftime("%Y-%m")

    # 创建默认的用户画像（如果未提供）
    if not profile:
        profile = "未知户型 | 未知居住情况 | 未知地面类型"

    # 创建初始记录数据
    initial_record = {
        "特征": profile,
        "效率": "新用户暂无清洁效率数据\n建议先进行首次清扫以收集数据",
        "耗材": "主刷寿命:全新\n滤网状态:全新\n边刷状态:全新",
        "对比": "新用户暂无对比数据"
    }

    # 添加到内存数据
    external_data[user_id] = {month: initial_record}

    # 持久化到CSV文件
    _save_user_to_csv(user_id, month, initial_record)

    return (
        f"已成功为新用户 {user_id} 创建初始报告数据。\n"
        f"用户画像：{profile}\n"
        f"初始月份：{month}\n"
        f"提示：建议用户先进行首次清扫，后续将自动更新使用数据。"
    )


def _update_csv_user_profile(user_id: str, new_profile: str):
    """更新CSV文件中指定用户的画像信息（特征字段）。"""
    external_data_path = get_abs_path(agent_conf["external_data_path"])

    # 读取所有数据
    rows = []
    with open(external_data_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row.get("用户ID") == user_id:
                row["特征"] = new_profile
            rows.append(row)

    # 重新写入文件
    with open(external_data_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"用户 {user_id} 的画像信息已更新并持久化到CSV文件")


@tool
def update_user_profile(input_data: str):
    """更新指定用户的画像信息。用于修改用户的房屋面积、居住情况、地面类型等基础特征。

    参数可以是两种格式：
    1. JSON字符串：{"user_id": "1011", "profile": "120㎡ | 有宠物 | 无地毯"}
    2. 单参数字符串（user_id）：配合 profile 参数（但建议用JSON格式）

    返回:
        更新结果信息
    """
    generate_external_data()

    # 解析输入，支持 JSON 格式
    user_id = ""
    profile = ""

    try:
        if input_data.startswith("{") and input_data.endswith("}"):
            data = json.loads(input_data)
            user_id = data.get("user_id", "").strip()
            profile = data.get("profile", "").strip()
        else:
            # 如果不是JSON，当作user_id处理（需要额外提供profile，此处报错提示）
            return "请使用JSON格式输入，如：{\"user_id\": \"1011\", \"profile\": \"120㎡ | 有宠物 | 无地毯\"}"
    except json.JSONDecodeError:
        return "JSON解析失败，请使用正确格式：{\"user_id\": \"1011\", \"profile\": \"120㎡ | 有宠物 | 无地毯\"}"

    if not user_id:
        return "用户ID不能为空。"

    if not profile:
        return "用户画像信息不能为空。"

    # 检查用户是否存在
    if user_id not in external_data:
        return f"用户 {user_id} 不存在，请先创建用户。可使用 create_user_report 工具创建新用户。"

    # 更新内存中所有月份的用户画像
    updated_months = []
    for month, record in external_data[user_id].items():
        record["特征"] = profile
        updated_months.append(month)

    # 持久化到CSV文件
    _update_csv_user_profile(user_id, profile)

    return (
        f"已成功更新用户 {user_id} 的画像信息。\n"
        f"新画像：{profile}\n"
        f"已更新月份：{', '.join(sorted(updated_months))}\n"
        f"提示：画像信息已同步更新到所有历史记录中。"
    )


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
def fetch_external_data(user_id: str, month: str = "", profile: str = "", auto_create: bool = True):
    """获取指定用户在指定月份的使用记录。当用户不存在时自动创建初始报告。

    参数:
        user_id: 用户ID（可以是数字格式或JSON字符串）
        month: 查询月份，为空时使用最近月份
        profile: 用户画像信息，仅在自动创建新用户时使用，格式如 "80㎡公寓 | 单身 | 木地板"
        auto_create: 是否在用户不存在时自动创建，默认为True

    可以传入 JSON 格式如 {"user_id": "1001", "month": "2025-12", "profile": "80㎡公寓"}。
    """
    generate_external_data()

    # 尝试解析 JSON 格式的输入
    try:
        if user_id.startswith("{") and user_id.endswith("}"):
            import json
            data = json.loads(user_id)
            user_id = data.get("user_id", "").strip()
            month = data.get("month", "").strip()
            profile = data.get("profile", "").strip()
    except (json.JSONDecodeError, AttributeError):
        pass

    if not user_id:
        return "用户ID不能为空。"

    # 当用户不存在时，自动创建新用户报告
    if user_id not in external_data:
        if auto_create:
            logger.info(f"用户 {user_id} 不存在，自动创建初始报告")
            create_result = create_user_report.invoke({"user_id": user_id, "profile": profile, "month": month})
            if "已成功" in create_result:
                # 创建成功后，返回新创建的数据
                current_month = month or datetime.now().strftime("%Y-%m")
                return (
                    f"{create_result}\n\n"
                    f"=== 新用户初始数据 ===\n"
                    f"{_format_record(user_id, current_month, external_data[user_id][current_month])}"
                )
            else:
                return create_result
        else:
            logger.warning(f"未能检索到用户{user_id}的使用数据")
            return "未检索到该用户的使用数据。如需创建新用户，请使用 create_user_report 工具。"

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
    'create_user_report', 'update_user_profile',
    'recommend_vacuum_robot', 'get_vacuum_brands', 'get_product_count',
]

# 注意：set_session_context 和 clear_session_context 不是工具，
# 它们是内部使用的上下文管理函数，不需要暴露给 LLM 作为可调用工具
