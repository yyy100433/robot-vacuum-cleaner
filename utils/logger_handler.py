from datetime import datetime, timedelta
import logging
import os
import re

from utils.path_tool import get_abs_path
LOG_ROOT = get_abs_path('logs')
os.makedirs(LOG_ROOT, exist_ok=True)
DEFAULT_LOG_FORMAT = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
)

def _cleanup_old_logs(log_root: str, keep_days: int = 3):
    """清理超过 keep_days 天的旧日志文件"""
    try:
        now = datetime.now()
        cutoff = now - timedelta(days=keep_days)

        # 匹配格式: nian-yue-ri.log (如: 2026年04月11日.log)
        pattern = re.compile(r'^(\d{4})-(\d{2})-(\d{2})\.log$')

        for filename in os.listdir(log_root):
            match = pattern.match(filename)
            if match:
                year, month, day = match.groups()
                try:
                    file_date = datetime(int(year), int(month), int(day))
                    if file_date < cutoff:
                        filepath = os.path.join(log_root, filename)
                        os.remove(filepath)
                except ValueError:
                    continue
    except Exception:
        pass

def get_logger(
        name: str = "agent",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        log_file: str = None,
) -> logging.Logger:
    """创建全局 logger，同时输出到控制台和日志文件。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加处理器，否则在 Streamlit 重跑时会重复打印日志。
    if logger.handlers:
        # 即使 handler 已存在，启动时仍执行一次日志清理
        _cleanup_old_logs(LOG_ROOT, keep_days=3)
        return logger

    # 控制台用于实时观察应用行为。
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOG_FORMAT)
    logger.addHandler(console_handler)

    # 文件日志更适合排查线上/回溯类问题。
    if not log_file:
        # 按天命名: nian-yue-ri.log
        log_file = os.path.join(LOG_ROOT, f"{datetime.now().strftime('%Y年%m月%d日')}.log")

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(file_level)
    file_handler.setFormatter(DEFAULT_LOG_FORMAT)
    logger.addHandler(file_handler)

    # 清理超过3天的旧日志
    _cleanup_old_logs(LOG_ROOT, keep_days=3)

    return logger

logger = get_logger()
if __name__ == '__main__':
    logger.info("信息日志")
    logger.error("错误日志")
    logger.debug("调试日志")
