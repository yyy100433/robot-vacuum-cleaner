# 扫地机器人推荐系统 - LangChain 集成版

## 项目结构

```
langchain-agent/
├── data/
│   └── products/                    # 产品数据目录
│       ├── schema.sql               # 数据库表结构
│       ├── robot_vacuum.db          # SQLite 数据库 (100 条产品数据)
│       ├── init_database.py         # 数据库初始化脚本
│       ├── recommender.py           # 推荐引擎核心模块
│       ├── tools.py                 # LangChain 工具定义
│       └── __init__.py              # Python 包标识
├── agent/
│   ├── react_agent.py               # AI Agent 主入口 (已集成推荐工具)
│   └── tools/
│       └── agent_tools.py           # 工具集合 (已导入推荐工具)
├── test_recommend_tool.py           # 推荐功能测试脚本
└── ...
```

## 已完成的工作

### 1. 数据库创建
- **位置**: `data/products/robot_vacuum.db`
- **数据量**: 100 条扫地机器人产品数据
- **品牌覆盖**: 小米、石头、科沃斯、云鲸、追觅、iRobot、美的、海尔 (8 个品牌)

### 2. 推荐引擎模块
- **文件**: `data/products/recommender.py`
- **功能**: 
  - 多维度筛选（品牌、吸力、导航、价格、续航、面积等）
  - 智能匹配分数计算
  - 支持必选功能过滤（拖地、自动回充）

### 3. LangChain 工具
- **文件**: `data/products/tools.py`
- **工具列表**:
  - `recommend_vacuum_robot`: 根据条件推荐产品
  - `get_vacuum_brands`: 获取品牌列表
  - `get_product_count`: 获取产品总数

### 4. AI Agent 集成
- **修改文件**: `agent/react_agent.py`
- **改动内容**: 
  - 导入推荐工具模块
  - 将推荐工具添加到 Agent 工具列表中

现在 AI Agent 可以理解并响应类似以下的用户请求：
- "我想买一个小米的扫地机器人"
- "推荐吸力大的扫地机器人"
- "有什么性价比高的扫地机器人推荐吗？"
- "帮我推荐一个适合大户型的扫地机器人"

## 使用方法

### 方法 1: 运行测试脚本
```bash
cd langchain-agent
.venv/Scripts/python.exe test_recommend_tool.py
```

### 方法 2: 启动 Streamlit Web 界面
```bash
cd langchain-agent
.venv/Scripts/streamlit.exe run app.py
```

然后在浏览器中访问 `http://localhost:8501`，通过对话方式获取推荐。

### 方法 3: 直接使用推荐工具
```python
from data.products.tools import recommend_vacuum_robot

result = recommend_vacuum_robot.invoke({
    'brand': '小米',
    'min_suction_power': 2000,
    'navigation_type': 'LDS 激光导航',
    'max_price': 4000,
    'house_area': 110,
    'limit': 5
})
print(result)
```

## 推荐参数说明

| 参数名 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| brand | str | 品牌偏好（小米/石头/科沃斯等） | None |
| min_suction_power | int | 最小吸力 (Pa) | 1500 |
| navigation_type | str | 导航类型偏好 | None |
| max_price | float | 最大预算 (元) | 5000 |
| min_battery_life | int | 最小续航 (分钟) | 60 |
| house_area | int | 房屋面积 (㎡) | 100 |
| need_mopping | bool | 需要拖地功能 | False |
| need_self_charging | bool | 需要自动回充 | False |
| limit | int | 返回数量 | 5 |

## 示例输出

```
为您推荐 3 款符合条件的扫地机器人：
============================================================

【推荐 #1】小米 米家扫拖机器人 4
----------------------------------------
  吸力：2727Pa
  导航：激光 + 视觉融合导航
  续航：210 分钟
  适用面积：150㎡
  价格：2487 元
  评分：4.7/5 (2289 条评价)
  拖地功能：支持
  自动回充：支持
  噪音：57dB
  匹配度：83.1%
```

## 注意事项

1. 数据库为模拟数据，实际购买请查阅官方最新参数
2. 如需更新数据，可重新运行 `data/products/init_database.py`
3. AI Agent 需要配置 DASHSCOPE_API_KEY 才能运行完整功能