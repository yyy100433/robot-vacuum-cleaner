# Redis 双层记忆系统使用示例

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                     Redis Memory Store                       │
├─────────────────────────────────────────────────────────────┤
│  L1: Short-term Memory (滑动窗口)                            │
│  ┌───────────────────────────────────────────────────┐      │
│  │ Sorted Set: memory:session:{id}:chat_history     │      │
│  │  Score = timestamp, Value = JSON(message)        │      │
│  │  - ZADD 按时间插入                                 │      │
│  │  - ZREMRANGEBYRANK 维护窗口大小 N                 │      │
│  └───────────────────────────────────────────────────┘      │
├─────────────────────────────────────────────────────────────┤
│  L2: Long-term Memory (持久化)                               │
│  ┌───────────────────────────────────────────────────┐      │
│  │ Hash: memory:session:{id}:profile                  │      │
│  │  - user_preferences: {language, tone, ...}        │      │
│  │  - summary: {key_points, insights, ...}           │      │
│  └───────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 安装依赖

```bash
pip install redis
```

### 2. 启动 Redis

```bash
# Docker 方式（推荐）
docker run -d -p 6379:6379 --name redis redis:latest

# 或直接启动本地 Redis 服务
redis-server
```

### 3. 环境变量配置（可选）

```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
export REDIS_DB=0
export MEMORY_WINDOW_SIZE=50    # 滑动窗口大小
export MEMORY_TTL=604800        # 7 天 TTL
```

## API 用法

### 基础会话管理

```python
from utils.chat_session_store import (
    create_session,
    update_session_messages,
    get_recent_context,
    delete_session,
)

# 创建新会话
session = create_session(title="Python 学习")
session_id = session["id"]

# 添加消息到会话
messages = [
    {"role": "user", "content": "Python 中列表推导式怎么用？"},
    {"role": "assistant", "content": "列表推导式语法：[expr for item in iterable]"}
]
updated_session = update_session_messages(session, messages)

# 获取最近 10 条消息作为 AI 回复的上下文
context = get_recent_context(session_id, count=10)
for msg in context:
    print(f"{msg['role']}: {msg['content']}")
```

### 用户偏好存储（L2 长期记忆）

```python
from utils.chat_session_store import set_user_preference, get_user_preference

# 保存用户偏好
set_user_preference(session_id, "language", "zh-CN")
set_user_preference(session_id, "coding_style", "pep8")
set_user_preference(session_id, "preferred_length", "concise")

# 读取用户偏好
lang = get_user_preference(session_id, "language", "en-US")
print(f"用户语言偏好：{lang}")
```

### 会话摘要（知识积累）

```python
from utils.chat_session_store import save_summary, get_summary

# 保存会话总结
summary = {
    "key_topics": ["列表推导式", "生成器表达式", "map/filter"],
    "user_level": "intermediate",
    "learning_goals": ["掌握 Python 函数式编程特性"]
}
save_summary(session_id, summary)

# 下次对话时加载摘要，实现跨会话连贯性
existing_summary = get_summary(session_id)
if existing_summary:
    print(f"之前的学习目标：{existing_summary['learning_goals']}")
```

### 直接操作 RedisMemoryStore

```python
from utils.memory_store import RedisMemoryStore

store = RedisMemoryStore(
    host="localhost",
    port=6379,
    window_size=30,  # 自定义滑动窗口大小
    ttl_seconds=86400 * 30  # 30 天过期
)

# 添加消息（自动维护滑动窗口）
store.add_message("session_123", "user", "你好")
store.add_message("session_123", "assistant", "有什么可以帮助你的？")

# 获取最近上下文
context = store.get_recent_context("session_123", count=10)

# 检查连接状态
if store.health_check():
    print("Redis 连接正常")
```

## 滑动窗口机制详解

```
消息流入 → ZADD → 超出窗口 → ZREMRANGEBYRANK → 自动淘汰最旧消息

时间轴:
T1: [msg1]                    (1 条)
T2: [msg1, msg2]              (2 条)
...
T50: [msg1, msg2, ..., msg50] (达到窗口上限)
T51: [msg2, msg3, ..., msg51] (msg1 被自动淘汰)
```

核心代码逻辑：
```python
# ZADD 按时间戳插入
self.client.zadd(key, {json.dumps(message): score})

# 超出窗口则移除最旧的一条
count = self.client.zcard(key)
if count > self.window_size:
    self.client.zremrangebyrank(key, 0, 0)  # 移除 rank 0 的最旧消息
```

## 数据持久化与清理

```python
# 手动删除整个会话
delete_session(sessions_list, session_id)

# 清空短期记忆但保留长期记忆
store.clear_history(session_id)

# Redis 会自动根据 TTL 过期清理数据
# 也可以手动设置定时任务清理过期键
```

## 性能优化建议

1. **批量操作**: 使用管道 `pipeline()` 减少网络往返
2. **窗口大小**: 根据上下文需求调整 `window_size`（建议 20-100）
3. **TTL 策略**: 根据业务需求设置合适的过期时间
4. **索引优化**: 为常用查询建立额外索引 Key
