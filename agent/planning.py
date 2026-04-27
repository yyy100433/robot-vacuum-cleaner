"""
任务规划模块 - 实现多步规划和子目标分解
"""
import json
import re
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.prompts import PromptTemplate
from model.factory import get_chat_model
from utils.logger_handler import logger


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 执行失败
    SKIPPED = "skipped"      # 已跳过


@dataclass
class SubTask:
    """子任务定义"""
    id: str                          # 任务唯一 ID
    description: str                 # 任务描述
    tool_name: Optional[str] = None  # 需要调用的工具名
    tool_input: Optional[str] = None # 工具输入参数
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他任务 ID
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None     # 执行结果
    error: Optional[str] = None      # 错误信息

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "result": self.result,
            "error": self.error
        }


@dataclass
class Plan:
    """执行计划"""
    original_query: str              # 原始用户问题
    thought: str                     # 规划思考过程
    subtasks: List[SubTask]          # 子任务列表
    current_step: int = 0            # 当前执行步骤

    def get_ready_tasks(self) -> List[SubTask]:
        """获取可以执行的子任务（依赖已满足且状态为 pending）"""
        completed_ids = {t.id for t in self.subtasks if t.status == TaskStatus.COMPLETED}
        return [
            t for t in self.subtasks
            if t.status == TaskStatus.PENDING
            and all(dep in completed_ids for dep in t.dependencies)
        ]

    def get_next_task(self) -> Optional[SubTask]:
        """获取下一个待执行的任务"""
        ready = self.get_ready_tasks()
        return ready[0] if ready else None

    def is_complete(self) -> bool:
        """检查计划是否全部完成"""
        return all(t.status in [TaskStatus.COMPLETED, TaskStatus.SKIPPED] for t in self.subtasks)

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "thought": self.thought,
            "subtasks": [t.to_dict() for t in self.subtasks],
            "current_step": self.current_step
        }


class TaskPlanner:
    """任务规划器 - 将用户问题分解为可执行的子任务"""

    PLANNING_PROMPT_TEMPLATE = """你是一个智能任务规划助手。请将用户的问题分解为一系列可执行的子任务。

## 可用的工具列表：
{tools_description}

## 规划规则：
1. 分析用户问题，识别需要哪些步骤来完成
2. 每个子任务应该是**单一职责**的（只调用一个工具或执行一个逻辑步骤）
3. 如果任务有依赖关系（如：需要先获取用户信息才能查询数据），请明确标注依赖
4. 如果可以直接回答（不需要工具），则只生成一个"直接回答"任务
5. 子任务数量控制在 1-5 个，避免过度分解

## 输出格式（必须严格按照以下 JSON 格式）：
```json
{{
    "thought": "对任务的思考过程，分析需要哪些步骤",
    "subtasks": [
        {{
            "id": "task_1",
            "description": "任务描述",
            "tool_name": "工具名或 null",
            "tool_input": "工具输入参数",
            "dependencies": []
        }},
        {{
            "id": "task_2",
            "description": "任务描述",
            "tool_name": "工具名或 null",
            "tool_input": "工具输入参数",
            "dependencies": ["task_1"]
        }}
    ]
}}
```

## 示例：
用户问题："我是用户 1001，请帮我查一下上个月的扫地机器人使用报告，并告诉我怎么保养"

```json
{{
    "thought": "用户需要：1) 查询用户 1001 的上个月使用报告 2) 基于报告数据给出保养建议。需要先获取用户数据，然后基于数据生成报告和建议",
    "subtasks": [
        {{
            "id": "task_1",
            "description": "获取用户 1001 的身份信息和基本画像",
            "tool_name": "get_user_id",
            "tool_input": "",
            "dependencies": []
        }},
        {{
            "id": "task_2",
            "description": "查询用户 1001 最近一个月的使用数据",
            "tool_name": "fetch_latest_external_data",
            "tool_input": "1001",
            "dependencies": ["task_1"]
        }},
        {{
            "id": "task_3",
            "description": "获取用户详细画像信息",
            "tool_name": "get_user_profile",
            "tool_input": "1001",
            "dependencies": ["task_1"]
        }},
        {{
            "id": "task_4",
            "description": "基于用户数据生成使用报告和保养建议",
            "tool_name": null,
            "tool_input": null,
            "dependencies": ["task_2", "task_3"]
        }}
    ]
}}
```

## 当前会话信息：
- 城市：{city}
- 用户 ID：{user_id}
- 历史对话：{chat_history}

请规划以下用户问题：
用户问题：{query}

请输出 JSON 格式的规划结果："""

    def __init__(self):
        self.llm = get_chat_model()
        self.planning_prompt = PromptTemplate.from_template(self.PLANNING_PROMPT_TEMPLATE)

    def _build_tools_description(self, tools: List[Any]) -> str:
        """构建工具描述"""
        descriptions = []
        for tool in tools:
            name = getattr(tool, 'name', str(tool))
            desc = getattr(tool, 'description', '无描述')
            descriptions.append(f"- {name}: {desc}")
        return "\n".join(descriptions)

    def _parse_plan(self, response: str, original_query: str) -> Plan:
        """解析 LLM 返回的规划结果"""
        # 提取 JSON 部分
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接提取 JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                json_str = response

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"解析规划 JSON 失败：{e}, 原始响应：{response[:200]}")
            # 创建默认计划
            return Plan(
                original_query=original_query,
                thought="解析规划失败，使用默认单步执行",
                subtasks=[SubTask(
                    id="task_1",
                    description="直接回答用户问题",
                    tool_name=None,
                    tool_input=None,
                    dependencies=[]
                )]
            )

        subtasks = []
        for i, task_data in enumerate(data.get("subtasks", [])):
            subtasks.append(SubTask(
                id=task_data.get("id", f"task_{i+1}"),
                description=task_data.get("description", ""),
                tool_name=task_data.get("tool_name"),
                tool_input=task_data.get("tool_input"),
                dependencies=task_data.get("dependencies", [])
            ))

        return Plan(
            original_query=original_query,
            thought=data.get("thought", ""),
            subtasks=subtasks
        )

    def create_plan(
        self,
        query: str,
        tools: List[Any],
        chat_history: str = "",
        city: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Plan:
        """为用户问题创建执行计划"""
        tools_desc = self._build_tools_description(tools)

        # 调用 LLM 生成规划
        prompt = self.planning_prompt.format(
            tools_description=tools_desc,
            query=query,
            chat_history=chat_history if chat_history else "无",
            city=city if city else "未知",
            user_id=user_id if user_id else "未知"
        )

        response = self.llm.invoke(prompt)
        content = response.content if hasattr(response, 'content') else str(response)

        logger.info(f"生成规划:\n{content[:500]}...")

        return self._parse_plan(content, query)


class PlanExecutor:
    """计划执行器 - 执行规划好的任务"""

    def __init__(self, tools: List[Any]):
        self.tools = {t.name: t for t in tools if hasattr(t, 'name')}
        self.tool_list = tools

    def _get_tool(self, name: Optional[str]) -> Optional[Any]:
        """获取指定名称的工具"""
        if not name:
            return None
        return self.tools.get(name)

    def _execute_tool(self, tool: Any, tool_input: Optional[str]) -> str:
        """执行工具调用"""
        try:
            if hasattr(tool, 'invoke'):
                result = tool.invoke(tool_input or "")
            elif callable(tool):
                result = tool(tool_input or "")
            else:
                result = str(tool)
            return str(result) if result else ""
        except Exception as e:
            logger.error(f"工具执行失败 {tool}: {e}")
            return f"工具执行失败：{str(e)}"

    def execute_step(self, plan: Plan) -> Optional[SubTask]:
        """执行计划的下一步"""
        task = plan.get_next_task()
        if not task:
            return None

        task.status = TaskStatus.RUNNING
        logger.info(f"执行任务：{task.id} - {task.description}")

        # 如果需要调用工具
        if task.tool_name:
            tool = self._get_tool(task.tool_name)
            if tool:
                task.result = self._execute_tool(tool, task.tool_input)
            else:
                task.error = f"工具 {task.tool_name} 不存在"
                task.status = TaskStatus.FAILED
                return task
        else:
            # 无工具任务，标记为完成，结果留空
            task.result = ""

        task.status = TaskStatus.COMPLETED
        plan.current_step += 1
        return task

    def execute_plan(self, plan: Plan) -> Dict[str, Any]:
        """执行完整计划，返回执行结果"""
        execution_log = []

        while not plan.is_complete():
            task = self.execute_step(plan)
            if task:
                execution_log.append({
                    "task": task.to_dict(),
                    "timestamp": logger.handlers[0].baseFilename if logger.handlers else None
                })

                # 如果任务失败，但还有其他任务，继续执行
                if task.status == TaskStatus.FAILED:
                    logger.warning(f"任务 {task.id} 失败，继续执行其他任务")

        return {
            "plan": plan.to_dict(),
            "execution_log": execution_log,
            "completed": plan.is_complete()
        }

    def get_execution_context(self, plan: Plan) -> str:
        """获取执行上下文，用于生成最终回答"""
        context_parts = []
        context_parts.append(f"原始问题：{plan.original_query}")
        context_parts.append(f"规划思路：{plan.thought}")
        context_parts.append("\n执行结果:")

        for task in plan.subtasks:
            status_icon = "✓" if task.status == TaskStatus.COMPLETED else "✗"
            context_parts.append(f"\n{status_icon} [{task.id}] {task.description}")
            if task.result:
                context_parts.append(f"   结果：{task.result[:200]}{'...' if len(str(task.result)) > 200 else ''}")
            if task.error:
                context_parts.append(f"   错误：{task.error}")

        return "\n".join(context_parts)


class PlanningReactAgent:
    """带规划能力的 ReAct Agent"""

    SUMMARIZE_PROMPT_TEMPLATE = """基于以下任务执行结果，生成对用户的最终回答。

## 原始问题
{original_query}

## 规划思路
{plan_thought}

## 任务执行详情
{execution_context}

## 生成规则
1. 直接回答用户的问题，不要提及任务 ID 或执行步骤
2. 整合所有子任务的结果，给出完整、连贯的回答
3. 如果某些任务失败，说明原因但不暴露技术细节
4. 保持友好、专业的语气
5. 如果有具体的工具结果（如天气、用户数据），直接呈现给用户

请生成最终回答："""

    # 用于判断是否需要 fallback 到 Coze 的关键词
    FALLBACK_KEYWORDS = [
        "未找到", "没有找到", "没有符合", "暂时没有",
        "目前没有找到", "很抱歉", "无法回答", "知识不足",
        "信息不足", "暂无数据", "未检索到", "检索不到"
    ]

    def __init__(self, tools: List[Any]):
        self.planner = TaskPlanner()
        self.executor = PlanExecutor(tools)
        self.llm = get_chat_model()
        self.summarize_prompt = PromptTemplate.from_template(self.SUMMARIZE_PROMPT_TEMPLATE)

    def _needs_fallback(self, text: str) -> bool:
        """检查回答是否需要 fallback 到 Coze"""
        if not text or len(text.strip()) < 10:
            return True
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.FALLBACK_KEYWORDS)

    def execute(
        self,
        query: str,
        chat_history: str = "",
        city: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> str:
        """
        执行带规划的 Agent 流程

        返回：最终回答
        """
        logger.info(f"开始规划执行，用户问题：{query}")

        # 1. 创建执行计划
        plan = self.planner.create_plan(
            query=query,
            tools=self.executor.tool_list,
            chat_history=chat_history,
            city=city,
            user_id=user_id
        )

        logger.info(f"生成计划，共 {len(plan.subtasks)} 个子任务")

        # 2. 执行计划
        self.executor.execute_plan(plan)

        # 3. 生成最终回答
        execution_context = self.executor.get_execution_context(plan)

        # 如果只有一个任务且没有工具调用，直接返回结果
        if len(plan.subtasks) == 1 and not plan.subtasks[0].tool_name:
            result = plan.subtasks[0].result or "我理解了您的问题，但我需要更多信息来回答。"
            # 检查结果是否需要 fallback
            if self._needs_fallback(result):
                success, answer = self._try_coze_fallback(query, chat_history)
                if success:
                    return answer
            return result

        # 否则使用 LLM 总结
        prompt = self.summarize_prompt.format(
            original_query=plan.original_query,
            plan_thought=plan.thought,
            execution_context=execution_context
        )

        response = self.llm.invoke(prompt)
        final_answer = response.content if hasattr(response, 'content') else str(response)

        logger.info(f"生成最终回答，长度：{len(final_answer)}")

        # 检查是否需要 fallback 到 Coze
        if self._needs_fallback(final_answer):
            logger.info(f"[INFO] 规划模式回答质量不足，触发 Coze fallback")
            success, answer = self._try_coze_fallback(query, chat_history)
            if success:
                return answer
            # Fallback 失败，返回原始回答

        return final_answer.strip()

    def execute_stream(
        self,
        query: str,
        chat_history: str = "",
        city: Optional[str] = None,
        user_id: Optional[str] = None
    ):
        """
        流式执行，输出规划过程和结果

        返回：生成器，yield 阶段性输出
        """
        # 1. 生成规划
        yield "🤔 正在分析您的问题...\n\n"

        plan = self.planner.create_plan(
            query=query,
            tools=self.executor.tool_list,
            chat_history=chat_history,
            city=city,
            user_id=user_id
        )

        yield f"📋 执行计划（{len(plan.subtasks)} 个步骤）:\n"
        for i, task in enumerate(plan.subtasks, 1):
            dep_info = f" [依赖：{','.join(task.dependencies)}]" if task.dependencies else ""
            tool_info = f" [工具：{task.tool_name}]" if task.tool_name else ""
            yield f"  {i}. {task.description}{tool_info}{dep_info}\n"

        yield "\n⏳ 开始执行...\n\n"

        # 2. 逐步执行
        results = {}
        while not plan.is_complete():
            task = plan.get_next_task()
            if not task:
                break

            task.status = TaskStatus.RUNNING
            yield f"▶️ 执行：{task.description}\n"

            # 准备工具输入（替换变量）
            tool_input = task.tool_input
            if tool_input and "{" in tool_input:
                # 简单变量替换
                for dep_id in task.dependencies:
                    if dep_id in results:
                        tool_input = tool_input.replace(f"{{{dep_id}}}", str(results[dep_id]))

            # 执行工具
            if task.tool_name:
                tool = self.executor._get_tool(task.tool_name)
                if tool:
                    try:
                        result = self.executor._execute_tool(tool, tool_input)
                        task.result = result
                        results[task.id] = result
                        yield f"   ✅ 完成：{result[:100]}{'...' if len(str(result)) > 100 else ''}\n\n"
                    except Exception as e:
                        task.error = str(e)
                        task.status = TaskStatus.FAILED
                        yield f"   ❌ 失败：{str(e)[:100]}\n\n"
                else:
                    task.error = f"工具 {task.tool_name} 不存在"
                    task.status = TaskStatus.FAILED
                    yield f"   ❌ 工具不存在\n\n"
            else:
                task.result = ""
                yield "\n"

            if task.status != TaskStatus.FAILED:
                task.status = TaskStatus.COMPLETED
            plan.current_step += 1

        yield "\n✅ 执行完成，生成回答...\n\n---\n\n"

        # 3. 生成最终回答
        execution_context = self.executor.get_execution_context(plan)
        prompt = self.summarize_prompt.format(
            original_query=plan.original_query,
            plan_thought=plan.thought,
            execution_context=execution_context
        )

        response = self.llm.invoke(prompt)
        final_answer = response.content if hasattr(response, 'content') else str(response)

        # 检查是否需要 fallback 到 Coze
        if self._needs_fallback(final_answer):
            logger.info(f"[INFO] 规划模式回答质量不足，触发 Coze fallback")
            # 先输出分隔线
            yield "\n⚠️ 本地知识库未能提供完整答案，正在联系 Coze 智能体...\n\n"
            success, answer = self._try_coze_fallback(query, chat_history)
            if success:
                yield answer
                return
            # Fallback 失败，返回原始回答
            yield "\n⚠️ Coze 智能体暂时不可用，以下是基于本地知识的回答：\n\n"

        yield final_answer.strip()


def should_use_planning(query: str, chat_history: str = "") -> bool:
    """
    判断是否需要使用规划模式

    简单问题：直接回答、单一工具调用
    复杂问题：需要多步、有依赖关系、需要整合多个信息源
    """
    # 复杂问题的关键词
    complex_keywords = [
        "报告", "分析", "对比", "推荐", "总结",
        "并且", "同时", "顺便", "还有",
        "基于", "根据", "结合",
        "先", "然后", "之后", "最后",
        "帮我查一下.*再.*",
        "用户.*月.*",
        "天气.*保养",
    ]

    query_lower = query.lower()

    for keyword in complex_keywords:
        if re.search(keyword, query_lower):
            return True

    # 如果历史对话中有上下文依赖
    if chat_history and len(chat_history) > 100:
        return True

    return False
