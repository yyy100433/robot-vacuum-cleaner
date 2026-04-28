"""
Coze 智能体服务类：当本地知识库无法回答时，调用 Coze API 获取回答，
并将回答自动保存到知识库中，同时增量更新向量检索库。

使用流式模式 (stream=True) 避免 PAT token 在轮询/消息获取时的认证问题。
"""
import os
import json
import requests
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from utils.logger_handler import logger
from utils.path_tool import get_abs_path
# 导入产品解析器
from data.products.coze_parser import CozeRobotParser

# 使用 TYPE_CHECKING 避免循环导入和启动时的强制初始化
if TYPE_CHECKING:
    from rag.vector_store import VectorStoreService


class CozeService:
    """Coze 智能体客户端服务。"""

    def __init__(
        self,
        token: Optional[str] = None,
        bot_id: Optional[str] = None,
        base_url: str = "https://api.coze.cn"
    ):
        """初始化 Coze 服务。

        Args:
            token: Coze API Token（支持 pat_ 和 cztei_ 格式）
            bot_id: Coze Bot ID
            base_url: Coze API 基础地址
        """
        self.token = token or os.getenv("COZE_API_TOKEN", "")
        self.bot_id = bot_id or os.getenv("COZE_BOT_ID", "")
        self.base_url = base_url
        self._parser = None
        self._vector_store = None  # 懒加载向量库服务

        if not self.token:
            logger.warning("Coze API Token 未配置，Coze fallback 功能将不可用")
        if not self.bot_id:
            logger.warning("Coze Bot ID 未配置，Coze fallback 功能将不可用")

    @property
    def parser(self) -> CozeRobotParser:
        """懒加载解析器"""
        if self._parser is None:
            self._parser = CozeRobotParser()
        return self._parser

    @property
    def vector_store(self) -> "VectorStoreService":
        """懒加载向量库服务"""
        if self._vector_store is None:
            # 延迟导入，避免启动时的强制初始化
            from rag.vector_store import VectorStoreService
            self._vector_store = VectorStoreService()
        return self._vector_store

    def _is_configured(self) -> bool:
        """检查 Coze 服务是否已正确配置。"""
        # 动态读取环境变量，确保在 .env 加载后能获取到配置
        self.token = self.token or os.getenv("COZE_API_TOKEN", "")
        self.bot_id = self.bot_id or os.getenv("COZE_BOT_ID", "")
        return bool(self.token and self.bot_id)

    def chat(self, query: str, chat_history: str = "") -> tuple[bool, str]:
        """
        调用 Coze 智能体进行对话（使用流式模式，避免 PAT token 认证问题）。

        Args:
            query: 用户问题
            chat_history: 对话历史（暂不支持，Coze 会自动管理）

        Returns:
            (success, answer): 是否成功，回答内容
        """
        if not self._is_configured():
            return False, "Coze 服务未配置"

        logger.info(f"开始调用 Coze，问题: {query[:50]}...")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        # 使用流式模式 (stream=True)，这样可以直接在创建对话时获取答案
        # 避免 PAT token 在轮询/消息获取时的认证问题
        # 生成固定的 conversation_id 确保对话连续性
        import uuid
        # 为每个用户生成固定的 conversation_id（基于用户ID或随机）
        conversation_id = f"fallback_{uuid.uuid5(uuid.NAMESPACE_DNS, 'coze_fallback_user').hex}"

        payload = {
            "bot_id": self.bot_id,
            "user_id": "fallback_user",
            "conversation_id": conversation_id,
            "stream": True,
            "auto_save_history": True,
            "additional_messages": [
                {
                    "role": "user",
                    "content": query,
                    "content_type": "text"
                }
            ],
        }

        try:
            response = requests.post(
                f"{self.base_url}/v3/chat",
                headers=headers,
                json=payload,
                timeout=60,
                stream=True
            )

            if response.status_code != 200:
                logger.error(f"Coze HTTP 错误: {response.status_code}")
                return False, f"Coze 服务 HTTP 错误: {response.status_code}"

            # 解析流式响应，收集答案
            answer = ""
            current_event = ""
            for line in response.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8")

                # 处理 event: 行
                if line_str.startswith("event:"):
                    current_event = line_str[6:].strip()
                    continue

                # 处理 data: 行
                if line_str.startswith("data:"):
                    data_str = line_str[5:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug(f"无法解析 JSON: {data_str[:100]}")
                        continue

                    # 确保 data 是字典
                    if not isinstance(data, dict):
                        logger.debug(f"解析的数据不是字典: {type(data)} - {str(data)[:100]}")
                        continue

                    # 根据当前事件处理数据
                    if current_event == "conversation.message.delta":
                        # 流式增量消息
                        if data.get("type") == "answer":
                            content = data.get("content", "")
                            if content:
                                answer += content
                    elif current_event == "conversation.message.completed":
                        # 消息完成
                        if data.get("type") == "answer":
                            content = data.get("content", "")
                            if content:
                                answer = content
                    elif current_event == "error":
                        # 错误事件
                        error_code = data.get("code", 0)
                        error_msg = data.get("msg", "未知错误")
                        logger.error(f"Coze 流式响应错误: code={error_code}, msg={error_msg}")
                        return False, f"Coze 服务错误: {error_msg}"

            if answer:
                logger.info(f"Coze 成功回答问题，回答长度: {len(answer)}")
                return True, answer
            else:
                logger.warning("Coze 返回空回答")
                return False, "Coze 返回空回答"

        except requests.exceptions.Timeout:
            logger.error("Coze API 请求超时")
            return False, "Coze 服务响应超时，请稍后重试"
        except requests.exceptions.RequestException as e:
            logger.error(f"Coze API 请求失败: {str(e)}")
            return False, f"Coze 服务请求失败: {str(e)}"
        except Exception as e:
            logger.error(f"Coze 调用异常: {str(e)}", exc_info=True)
            return False, f"Coze 调用异常: {str(e)}"

    def _get_knowledge_file(self, query: str) -> str:
        """
        根据用户问题判断应该保存到哪个知识库文件。

        Args:
            query: 用户问题

        Returns:
            对应的知识库文件名
        """
        # 故障排除相关关键词
        fault_keywords = [
            '故障', '错误', '报错', '异常', '失灵', '坏了', '不工作', '没反应',
            '无法启动', '不能', '不能正常', '出问题', '故障代码',
            '不回充', '回不了充', '回不去', '迷路', '卡住', '缠头发', '不出水',
            '不喷水', '不吸水', '不扫', '不拖', '不清洁', '警报', '提示音',
            '滴滴声', '红灯', '黄灯', '闪灯', '黑屏', '屏幕不亮', '无法连接',
            'wifi 连接', 'app 连不上', '离线', '掉线', '断网'
        ]
        if any(kw in query for kw in fault_keywords):
            return "coze_fallback_故障排除.txt"

        # 维护保养相关关键词
        maintenance_keywords = [
            '保养', '维护', '清理', '清洗', '怎么洗',
            '怎么清理', '多久清理', '定期', '滤网', '主刷', '边刷', '滚刷',
            '传感器', '轮子', '水箱', '拖布', '集尘袋', '耗材', '寿命', '替换'
        ]
        if any(kw in query for kw in maintenance_keywords):
            return "coze_fallback_维护保养.txt"

        # 选购相关关键词 - 保存到专门的 coze 推荐文件，不写入选购指南.txt
        purchase_keywords = [
            '推荐', '哪款', '哪个', '买什么', '怎么选', '选什么', '性价比',
            '排行榜', '对比', '哪个好', '适合', '购买', '值得', '型号', '新款'
        ]


    def save_answer_to_knowledge(
        self,
        query: str,
        answer: str,
        source_file: str = None,
        auto_update_vector_store: bool = True
    ) -> bool:
        """
        将 Coze 回答保存到知识库，并自动增量更新向量检索库。
        根据问题类型自动选择保存到对应的知识库文件。

        Args:
            query: 用户原始问题
            answer: Coze 返回的回答
            source_file: 保存的文件名（如指定则覆盖自动判断）
            auto_update_vector_store: 是否自动更新向量库，默认为 True

        Returns:
            是否保存成功
        """
        if not query or not answer:
            return False

        try:
            # 知识库数据目录
            data_dir = get_abs_path("data")
            if not data_dir:
                logger.error("无法获取知识库数据目录")
                return False

            # 根据问题类型自动选择目标文件
            if source_file is None:
                source_file = self._get_knowledge_file(query)

            file_path = os.path.join(data_dir, source_file)

            # 格式化问答记录
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            qa_record = f"\n\n【问答记录 - {timestamp}】\n问题：{query}\n回答：{answer}\n"

            # 追加写入文件
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(qa_record)

            logger.info(f"已将 Coze 回答保存到知识库文件: {file_path}")

            # 自动增量更新向量库
            if auto_update_vector_store:
                self._update_vector_store_for_file(source_file)

            return True

        except Exception as e:
            logger.error(f"保存 Coze 回答到知识库失败: {str(e)}", exc_info=True)
            return False

    def _update_vector_store_for_file(self, source_file: str) -> bool:
        """
        增量更新向量库：将指定文件的新内容加入向量检索。

        Args:
            source_file: 知识库文件名（相对于 data 目录）

        Returns:
            是否更新成功
        """
        try:
            # 获取文件绝对路径
            from utils.path_tool import get_abs_path
            data_dir = get_abs_path("data")
            if not data_dir:
                logger.error("无法获取知识库数据目录")
                return False

            file_path = os.path.join(data_dir, source_file)

            # 读取清单，删除指定文件的记录，强制重新加载该文件
            manifest = self.vector_store._load_manifest()
            relative_source = os.path.relpath(file_path, data_dir)

            if relative_source in manifest:
                logger.info(f"强制重新加载文件：{relative_source}")
                # 从清单中移除该文件，确保下次加载时会重新处理
                del manifest[relative_source]
                self.vector_store._save_manifest(manifest)

            # 重新加载所有文档，但指定文件会被强制重新处理
            self.vector_store.load_document(force_reload=False)

            logger.info(f"已将 {source_file} 的内容增量更新到向量库")
            return True
        except Exception as e:
            logger.error(f"增量更新向量库失败: {str(e)}", exc_info=True)
            return False

    def chat_and_save(self, query: str, chat_history: str = "") -> tuple[bool, str]:
        """
        调用 Coze 并自动保存回答到知识库，同时解析并保存产品到数据库。

        Args:
            query: 用户问题
            chat_history: 对话历史

        Returns:
            (success, answer): 是否成功，回答内容
        """
        success, answer = self.chat(query, chat_history)

        if success and answer:
            # 成功获取回答后，自动保存到知识库
            self.save_answer_to_knowledge(query, answer)

            # ✅ 修复：解析产品数据，不依赖返回结果，即使解析失败也不影响回答返回
            try:
                # 强制保存产品，不依赖用户问题类型
                result = self.parse_and_save_products(
                    answer,
                    source_id=f"coze_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    original_query=query,
                    force_save=True
                )
                # 即使解析失败也不影响回答
                if result.get("parsed", 0) > 0:
                    logger.info(f"成功解析并保存 {result['parsed']} 款产品到数据库")
            except Exception as e:
                logger.error(f"解析保存产品失败（不影响回答）: {e}")

        return success, answer


    def parse_and_save_products(self, coze_response: str, source_id: str = None, original_query: str = None,
                                force_save: bool = False) -> dict:
        """
        解析 Coze 返回的扫地机器人推荐数据并保存到 robot_vacuum.db 数据库。

        Args:
            coze_response: Coze 返回的文本
            source_id: 来源ID
            original_query: 原始用户问题
            force_save: 是否强制保存产品（即使原问题不是产品相关问题）

        Returns:
            保存结果统计
        """
        if not coze_response:
            return {"parsed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        try:
            print(f"[DEBUG] parse_and_save_products 被调用，响应长度：{len(coze_response)}")
            print(f"[DEBUG] 响应前500字符：{coze_response[:500]}")

            # 生成来源 ID
            if not source_id:
                source_id = f"coze_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # 首先尝试从原始回答中解析产品
            products = self.parser.parse(coze_response)
            parsed_count = len(products)
            print(f"[DEBUG] 从原始回答解析结果：{parsed_count} 款产品")

            # ✅ 修复：检查响应是否包含产品推荐特征，即使解析器没解析出来
            has_product_pattern = any([
                "## 推荐" in coze_response,
                "推荐一：" in coze_response or "推荐二：" in coze_response,
                "**推荐" in coze_response,
                "科沃斯" in coze_response and ("吸力" in coze_response or "价格" in coze_response),
                "石头" in coze_response and ("吸力" in coze_response or "价格" in coze_response),
                "追觅" in coze_response and ("吸力" in coze_response or "价格" in coze_response),
            ])

            # 如果解析器没解析出产品，但响应明显包含产品推荐，使用备用解析
            if parsed_count == 0 and has_product_pattern:
                logger.info("检测到产品推荐内容但解析器未识别，使用备用解析...")
                print("[DEBUG] 使用备用解析器提取产品")
                products = self._fallback_parse_products(coze_response)
                parsed_count = len(products)
                print(f"[DEBUG] 备用解析结果：{parsed_count} 款产品")

            # 检查 Coze 是否返回了"找不到"之类的回答
            no_answer_keywords = ['暂未找到', '没有找到', '抱歉', '无法推荐', '暂无', '不了解', '请提供','技术问题','重试']
            is_no_answer = any(kw in coze_response for kw in no_answer_keywords) and parsed_count == 0

            # 如果没有解析到产品，或者 Coze 说找不到，则主动请求产品推荐
            if (parsed_count == 0 or is_no_answer) and original_query:
                # 只要是关于产品的问题，或者强制保存，就主动获取推荐
                product_keywords = ['推荐', '哪款', '哪个', '买什么', '怎么选', '排行榜', '性价比', '机器人', '扫地机',
                                    '扫拖', '清洁']
                is_product_question = any(kw in original_query for kw in product_keywords)

                if force_save or is_product_question or is_no_answer:
                    # 主动调用 Coze 获取产品推荐
                    logger.info("Coze 回答中无产品信息或表示找不到，主动请求产品推荐...")
                    recommend_query = "请推荐几款扫地机器人，用 Markdown 格式返回，包含品牌、型号、价格、吸力、续航等参数。必须返回具体的产品推荐。"
                    success, recommend_answer = self.chat(recommend_query)

                    if success and recommend_answer:
                        logger.info(f"Coze 产品推荐回答长度：{len(recommend_answer)}")
                        # 解析产品推荐数据
                        products = self.parser.parse(recommend_answer)
                        parsed_count = len(products)
                        print(f"[DEBUG] 从产品推荐解析结果：{parsed_count} 款产品")

                        # 如果还是解析失败，使用备用解析
                        if parsed_count == 0:
                            products = self._fallback_parse_products(recommend_answer)
                            parsed_count = len(products)
                            print(f"[DEBUG] 备用解析（推荐）结果：{parsed_count} 款产品")


            # ✅ 修复：如果解析出产品，打印详细信息
            if products:
                logger.info(f"从 Coze 回复中解析出 {parsed_count} 款扫地机器人产品")
                for i, p in enumerate(products[:3]):
                    print(f"[DEBUG] 产品{i + 1}: {p.get('brand', '')} {p.get('model', '')} - ¥{p.get('price', '未知')}")

                # 保存到 robot_vacuum.db 数据库
                inserted, skipped, errors = self.parser.save_to_database(products, source_id)

                result = {
                    "parsed": parsed_count,
                    "inserted": inserted,
                    "skipped": skipped,
                    "errors": errors
                }

                logger.info(f"Coze 产品保存完成：插入{inserted}款，跳过{skipped}款，错误{errors}款")
                return result
            else:
                logger.info("Coze 回复中未检测到扫地机器人产品信息")
                print(f"[DEBUG] Coze 响应预览：{coze_response[:300]}")
                return {"parsed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        except Exception as e:
            logger.error(f"解析 Coze 产品数据失败：{e}", exc_info=True)
            return {"parsed": 0, "inserted": 0, "skipped": 0, "errors": 1}

    def _fallback_parse_products(self, text: str) -> list:
        """
        备用解析器：当主解析器失败时，使用正则表达式提取产品信息。
        """
        import re
        products = []

        # 匹配推荐块
        # 格式: ## 推荐一：科沃斯X2 Pro（约6999元） 或 科沃斯X2 Pro（约6999元）
        patterns = [
            # 匹配 "推荐一：XXX（约价格）" 格式
            r'(?:##\s*)?推荐[一二三][：:]\s*([^（\n]+)(?:（[约]*\s*(\d+)\s*元）)?',
            # 匹配 "**推荐一**：XXX" 格式
            r'\*\*推荐[一二三]\*\*[：:]\s*([^（\n]+)(?:（[约]*\s*(\d+)\s*元）)?',
            # 直接匹配品牌+型号
            r'([科沃斯|石头|追觅|小米|云鲸|iRobot|美的|海尔]+\s*[A-Z0-9\s]+?)(?:（[约]*\s*(\d+)\s*元）)?',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    name = match[0].strip() if match[0] else ""
                    price = match[1] if len(match) > 1 and match[1] else ""
                else:
                    name = match.strip()
                    price = ""

                if name and len(name) > 3:
                    # 提取品牌
                    brand = ""
                    for b in ["科沃斯", "石头", "追觅", "小米", "云鲸", "iRobot", "美的", "海尔"]:
                        if b in name:
                            brand = b
                            break

                    # 提取吸力
                    suction_match = re.search(r'(\d+)\s*Pa', text)
                    suction = suction_match.group(1) if suction_match else ""

                    # 提取续航
                    battery_match = re.search(r'(\d+)\s*分钟', text)
                    battery = battery_match.group(1) if battery_match else ""

                    product = {
                        "brand": brand,
                        "model": name[:100],
                        "price": int(price) if price.isdigit() else None,
                        "suction_power": int(suction) if suction else None,
                        "battery_life": int(battery) if battery else None,
                        "source": "coze_fallback"
                    }
                    products.append(product)

                    if len(products) >= 5:  # 最多5个
                        break

            if products:
                break

        return products
# 创建全局实例，使用环境变量或默认配置
coze_service = CozeService()


if __name__ == '__main__':
    # 测试 Coze 服务
    import os
    token = os.getenv("COZE_API_TOKEN", "pat_UStMuyH7a5xdPqmIMQPqdFtHBdf1Kr7cqw8R2h2WH74a1qJQFn7XPql4VZOGex2Y")
    bot_id = os.getenv("COZE_BOT_ID", "7627787554993340456")
    service = CozeService(token=token, bot_id=bot_id)
    success, answer = service.chat("扫地机器人不回充怎么解决？")
    print(f"Success: {success}")
    print(f"Answer: {answer}")