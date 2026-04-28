import os
import re
from typing import Iterable, Optional, Generator

from langchain.agents import create_agent
from langchain_classic.agents import AgentExecutor
from langchain_core.prompts import PromptTemplate

from model.factory import get_chat_model
from utils.logger_handler import logger
from agent.tools.agent_tools import (
    rag_summarize,
    get_weather,
    get_user_location,
    get_user_id,
    get_current_month,
    list_report_months,
    fetch_latest_external_data,
    get_user_profile,
    fetch_external_data,
    fill_context_for_report,
    set_session_context,
    clear_session_context,
    # 知识库管理工具
    list_knowledge_files,
    get_knowledge_file_detail,
    add_knowledge_from_file,
    add_knowledge_from_text,
    delete_knowledge_file,
    delete_knowledge_chunk,
    search_knowledge,
    list_knowledge_chunks,
    get_knowledge_chunk_detail,
    update_knowledge_content,
)
# 导入扫地机器人推荐工具
from data.products.tools import recommend_vacuum_robot, get_vacuum_brands, get_product_count
# 导入规划模块
from agent.planning import PlanningReactAgent, should_use_planning
from services.coze_service import coze_service


class ReactAgent:
    """Agent 主入口，负责组装模型、工具和会话级上下文。"""
    COMMON_CITIES = [
        "北京", "上海", "广州", "深圳", "杭州", "苏州", "南京", "成都", "重庆", "天津",
        "武汉", "西安", "长沙", "青岛", "宁波", "厦门", "郑州", "合肥", "福州", "济南",
    ]
    INVALID_CITY_VALUES = {"哪个城市", "什么城市", "哪座城市", "哪个市", "哪里", "哪儿"}

    # 报告相关关键词
    REPORT_KEYWORDS = ["报告", "生成报告", "使用报告", "查看记录", "使用情况", "个人报告"]

    def __init__(self, enable_planning: bool = True):
        """初始化可流式执行的 Agent。"""
        # 1. 初始化 LLM
        self.llm = get_chat_model()

        # 2. 准备工具列表
        self.tools = [
            rag_summarize,
            get_weather,
            get_user_location,
            get_user_id,
            get_current_month,
            list_report_months,
            fetch_latest_external_data,
            get_user_profile,
            fetch_external_data,
            fill_context_for_report,
            # 知识库管理工具
            list_knowledge_files,
            get_knowledge_file_detail,
            add_knowledge_from_file,
            add_knowledge_from_text,
            delete_knowledge_file,
            delete_knowledge_chunk,
            search_knowledge,
            list_knowledge_chunks,
            get_knowledge_chunk_detail,
            update_knowledge_content,
            # 扫地机器人推荐工具
            recommend_vacuum_robot,
            get_vacuum_brands,
            get_product_count,
        ]

        # 3. 加载提示词模板
        self.base_prompt = self._load_prompt_template("main_prompt.txt")
        self.report_prompt = self._load_prompt_template("report_prompt.txt")

        # 创建基础的 ReAct 提示模板
        react_template = self._build_react_template(self.base_prompt)
        self.prompt = PromptTemplate.from_template(react_template)

        # 4. 初始化规划 Agent（如果启用）
        self.enable_planning = enable_planning
        self.planning_agent = PlanningReactAgent(self.tools) if enable_planning else None

    @staticmethod
    def _load_prompt_template(filename: str) -> str:
        """从 prompts 目录加载提示词文件。"""
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        filepath = os.path.join(prompts_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            # 如果文件不存在，返回默认提示词
            return """你是一个智能助手，可以使用以下工具来帮助用户解决问题。

工具列表：
{tools}

工具名称：{tool_names}

请严格按照以下格式回答：
Question: 用户的问题
Thought: 思考需要做什么
Action: 要使用的工具名称，必须是 [{tool_names}] 中的一个
Action Input: 传递给工具的输入参数
Observation: 工具返回的结果
... (这个 Thought/Action/Action Input/Observation 可以重复多次)
Thought: 我现在知道最终答案了
Final Answer: 对用户的最终回答"""

    def _build_react_template(self, system_prompt: str) -> str:
        """构建 ReAct 格式的提示词模板。"""
        return f"""{system_prompt}

工具列表：
{{tools}}

工具名称：{{tool_names}}

工具参数使用规则：
1. 单参数工具（user_id, city 等）：直接传入参数值，如 "1001" 或 "北京"
2. 无参数工具（get_user_id, get_current_month, get_user_location, fill_context_for_report）：传入空字符串 ""
3. 多参数工具（fetch_external_data）：传入 JSON 格式字符串，如 "user_id":"1001", "month":"2025-12"

请严格按照以下格式回答：
Question: 用户的问题
Thought: 思考需要做什么
Action: 要使用的工具名称，必须是 [{{tool_names}}] 中的一个
Action Input: 传递给工具的输入参数
Observation: 工具返回的结果
... (这个 Thought/Action/Action Input/Observation 可以重复多次)
Thought: 我现在知道最终答案了
Final Answer: 对用户的最终回答

开始！

{{chat_history}}
Question: {{input}}
Thought: {{agent_scratchpad}}"""

    def _is_report_request(self, input_text: str) -> bool:
        """判断用户请求是否为报告生成请求。"""
        input_lower = input_text.lower()
        return any(keyword in input_lower for keyword in self.REPORT_KEYWORDS)

    def _get_agent_executor(self, input_text: str) -> AgentExecutor:
        """根据输入文本获取对应的 AgentExecutor。"""
        is_report = self._is_report_request(input_text)

        if is_report:
            # 使用报告专用提示词
            report_react_template = self._build_react_template(self.report_prompt)
            prompt = PromptTemplate.from_template(report_react_template)
        else:
            # 使用基础提示词
            prompt = self.prompt

        agent = create_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=prompt,
        )

        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=True,
            handle_parsing_errors=True,
            max_iterations=15,  # 报告场景可能需要更多步骤
        )

    @staticmethod
    def _normalize_messages(messages: Iterable[dict]) -> list[dict]:
        """过滤无效消息，只保留 Agent 能消费的 user/assistant 文本。"""
        normalized = []
        for message in messages:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _build_chat_history(messages: list[dict]) -> str:
        """将历史消息拼接成对话格式，供 LLM 理解上下文。"""
        if len(messages) <= 1:
            return ""
        history_lines = []
        for msg in messages[:-1]:  # 排除最后一条（当前问题）
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                history_lines.append(f"用户：{content}")
            elif role == "assistant":
                history_lines.append(f"助手：{content}")
        return "\n".join(history_lines)

    @classmethod
    def _extract_session_facts(cls, messages: list[dict]) -> dict:
        """从历史消息里提取稳定事实。"""
        facts = {}

        for message in messages:
            content = (message.get("content") or "").strip()
            if not content:
                continue

            for city in cls.COMMON_CITIES:
                if city in content:
                    facts["city"] = city

            city_match = re.search(
                r"(?:在 | 住在|来自 | 位于)([^\s，。！？,.!?]{2,12}(?:市 | 县|区|北京|上海|广州|深圳|杭州|苏州|南京|成都|重庆|天津|武汉|西安|长沙|青岛|宁波|厦门|郑州|合肥|福州|济南))",
                content,
            )
            if city_match:
                candidate_city = city_match.group(1)
                if candidate_city not in cls.INVALID_CITY_VALUES:
                    facts["city"] = candidate_city

            user_id_match = re.search(r"(?:用户 ID|ID|id)[：:\s]*([0-9]{3,})", content)
            if user_id_match:
                facts["user_id"] = user_id_match.group(1)

        return facts

    def _should_use_planning(self, input_text: str, chat_history: str = "") -> bool:
        """判断是否需要使用规划模式。"""
        if not self.enable_planning or not self.planning_agent:
            return False
        return should_use_planning(input_text, chat_history)

    def execute_stream(self, messages: list[dict]):
        """执行一次带历史上下文的流式对话（逐字输出）。"""
        normalized_messages = self._normalize_messages(messages)
        session_facts = self._extract_session_facts(normalized_messages)

        # 获取最后一条用户消息作为 input
        last_user_message = None
        for msg in reversed(normalized_messages):
            if msg.get("role") == "user":
                last_user_message = msg.get("content", "")
                break

        if not last_user_message:
            yield from self._stream_char_by_char("没有找到用户消息")
            return

        # 构建对话历史
        chat_history = self._build_chat_history(normalized_messages)

        # 设置工具调用的会话上下文，让工具能获取历史信息
        set_session_context(
            city=session_facts.get("city"),
            user_id=session_facts.get("user_id"),
            chat_history=chat_history
        )

        # 判断是否需要使用规划模式
        use_planning = self._should_use_planning(last_user_message, chat_history)

        if use_planning:
            # 使用规划模式执行
            yield from self._execute_with_planning(
                last_user_message, chat_history, session_facts
            )
        else:
            # 使用传统 ReAct 模式执行
            yield from self._execute_with_react(
                last_user_message, chat_history, session_facts
            )

        # 清理会话上下文
        clear_session_context()

    def _stream_char_by_char(self, text: str) -> Generator[str, None, None]:
        """逐字符流式输出文本。"""
        for char in text:
            yield char

    def _execute_with_planning(
        self,
        query: str,
        chat_history: str,
        session_facts: dict
    ):
        """使用规划模式执行（逐字流式输出）"""
        try:
            # 流式执行规划
            for chunk in self.planning_agent.execute_stream(
                query=query,
                chat_history=chat_history,
                city=session_facts.get("city"),
                user_id=session_facts.get("user_id")
            ):
                # 逐字输出每个 chunk
                for char in chunk:
                    yield char
        except Exception as e:
            # 规划执行失败，回退到传统模式
            print(f"[INFO] 规划执行失败，回退到 ReAct 模式：{e}")
            yield from self._execute_with_react(query, chat_history, session_facts)

    def _execute_with_react(
        self,
        query: str,
        chat_history: str,
        session_facts: dict
    ):
        """使用传统 ReAct 模式执行（逐字流式输出）"""
        # 将会话中提取的事实融入问题（如城市、用户 ID）
        enhanced_input = query
        if session_facts.get("city"):
            enhanced_input = f"[用户所在城市：{session_facts['city']}] {enhanced_input}"
        if session_facts.get("user_id"):
            enhanced_input = f"[用户 ID：{session_facts['user_id']}] {enhanced_input}"

        # AgentExecutor 需要的输入格式
        input_dict = {
            "input": enhanced_input,
            "chat_history": chat_history,
        }

        try:
            # 根据输入场景获取对应的 AgentExecutor
            agent_executor = self._get_agent_executor(query)

            # 使用 stream 进行真正的流式执行，逐字输出
            full_output = []
            for chunk in agent_executor.stream(input_dict):
                # chunk 是包含 output 的 dict
                if isinstance(chunk, dict) and "output" in chunk:
                    output = chunk["output"]
                    if not output:
                        output = "我目前无法回答这个问题。"
                    full_output.append(output)

            # 组合完整输出
            combined_output = "".join(full_output)

            # 检查是否返回了 fallback 标记（支持多种格式）
            if "__FALLBACK_REQUIRED__" in combined_output or "FALLBACK_REQUIRED" in combined_output:
                logger.info(f"检测到 fallback 标记，调用 Coze 处理：{query}")
                # 调用 Coze 并保存结果
                success, coze_answer = coze_service.chat_and_save(query, chat_history)
                if success and coze_answer:
                    # 流式输出 Coze 的答案
                    for char in coze_answer:
                        yield char
                else:
                    yield "抱歉，智能客服暂时无法响应，请稍后重试。"
            else:
                # 正常输出工具返回的结果
                for char in combined_output:
                    yield char

        except Exception as e:
            error_msg = f"执行出错：{str(e)}"
            for char in error_msg:
                yield char


if __name__ == '__main__':
    agent = ReactAgent()
    res = agent.execute_stream([{"role": "user", "content": "扫地机器人在我所在地区的气温下如何保养"}])
    for chunk in res:
        print(chunk, end="", flush=True)
