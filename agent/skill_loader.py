import os
import yaml
from typing import Dict, List, Optional
from pathlib import Path
from utils.logger_handler import logger


class SkillLoader:
    """Skill 加载器，负责读取 SKILL.md 并注入到 Agent 提示词"""

    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(__file__).parent.parent / skills_dir
        self.config_path = Path(__file__).parent.parent / "config" / "skills.yaml"
        self.skills: Dict[str, dict] = {}
        self.config: Dict[str, dict] = {}
        self._load_config()
        self._load_all_skills()

    def _load_config(self):
        """加载技能配置文件"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                    self.config = config_data.get("skills", {})
                logger.info(f"加载技能配置: {self.config_path}")
            except Exception as e:
                logger.warning(f"加载技能配置失败: {e}")
                self.config = {}

    def _is_skill_enabled(self, skill_name: str) -> bool:
        """检查技能是否启用"""
        skill_config = self.config.get(skill_name, {})
        return skill_config.get("enabled", True)

    def _load_all_skills(self):
        """扫描 skills 目录，加载所有 SKILL.md"""
        if not self.skills_dir.exists():
            logger.warning(f"Skills 目录不存在: {self.skills_dir}")
            return

        for skill_path in self.skills_dir.iterdir():
            if skill_path.is_dir():
                skill_md = skill_path / "SKILL.md"
                if skill_md.exists():
                    skill_name = skill_path.name
                    if self._is_skill_enabled(skill_name):
                        self._parse_skill(skill_name, skill_md)
                    else:
                        logger.info(f"技能 {skill_name} 已禁用，跳过加载")

    def _parse_skill(self, skill_name: str, skill_md_path: Path):
        """解析 SKILL.md，提取元数据和正文"""
        try:
            content = skill_md_path.read_text(encoding="utf-8")

            # 解析 YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    body = parts[2]
                    metadata = yaml.safe_load(frontmatter) or {}
                else:
                    metadata = {"name": skill_name, "description": ""}
                    body = content
            else:
                metadata = {"name": skill_name, "description": ""}
                body = content

            self.skills[skill_name] = {
                "name": metadata.get("name", skill_name),
                "description": metadata.get("description", ""),
                "priority": self.config.get(skill_name, {}).get("priority", 99),
                "content": body.strip(),
                "path": str(skill_md_path)
            }
            logger.info(f"加载技能: {skill_name} - {self.skills[skill_name]['description'][:50]}")
        except Exception as e:
            logger.error(f"加载技能 {skill_name} 失败: {e}")

    def get_skill_summaries(self) -> str:
        """获取所有技能的摘要（名称+描述），用于注入到主提示词"""
        summaries = []
        # 按优先级排序
        sorted_skills = sorted(self.skills.items(), key=lambda x: x[1].get("priority", 99))
        for skill_name, skill_info in sorted_skills:
            summaries.append(f"- **{skill_info['name']}**: {skill_info['description']}")
        return "\n".join(summaries) if summaries else "暂无可用技能"

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        """获取指定技能的完整内容"""
        skill = self.skills.get(skill_name)
        return skill["content"] if skill else None

    def match_skill(self, user_query: str) -> Optional[str]:
        """根据用户问题匹配最合适的技能"""
        user_query_lower = user_query.lower()

        # 关键词匹配规则（按优先级排序）
        skill_keywords = {
            "product-recommendation": ["推荐", "买", "选购", "预算", "品牌", "型号", "哪款", "什么牌子", "怎么选", "选择"],
            "fault-diagnosis": ["故障", "问题", "不回充", "卡住", "异常", "报警", "出错", "报错", "坏了", "不动", "不工作", "异响", "噪音"],
            "report-generation": ["报告", "使用情况", "统计数据", "查看记录", "月报", "分析", "使用记录", "个人报告"],
            "maintenance": ["保养", "维护", "清理", "更换", "寿命", "延长", "清洗", "耗材", "滤网", "主刷"]
        }

        # 按优先级检查匹配
        sorted_skill_names = sorted(
            self.skills.keys(),
            key=lambda x: self.skills[x].get("priority", 99)
        )

        for skill_name in sorted_skill_names:
            if skill_name in skill_keywords:
                keywords = skill_keywords[skill_name]
                if any(kw in user_query_lower for kw in keywords):
                    return skill_name

        return None

    def get_all_skill_names(self) -> List[str]:
        """获取所有已加载技能的名称列表"""
        return list(self.skills.keys())


# 全局实例
skill_loader = SkillLoader()