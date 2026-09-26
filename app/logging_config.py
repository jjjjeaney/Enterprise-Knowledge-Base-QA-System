"""Loguru-based logging setup shared across the app.

日志约定（新增日志请照这个来）：

- 正文用中文，字段名用英文 snake_case：`logger.info("混合检索完成 returned={} elapsed_ms={:.1f}", n, ms)`。
  英文 key 是为了可机读，正文保持中文以和全库注释、前端文案一致。
- 用 loguru 的 `{}` 占位符，不要 f-string：级别被过滤时不白做一次字符串格式化。
- 一条日志一个事件，单行，结尾不加标点，不用省略号。不要把多个字段用 `\\n` 拼成一条。
- 关联标识（thread_id / job_id / item_id / worker_id）不写进正文，用
  `logger.contextualize(...)` 绑定，由下面的 patcher 统一渲染到行尾。绑定点只在入口一处：
  `app/api/routes_chat.py` 绑 thread_id、`app/worker.py` 绑 worker_id/item_id、
  `app/api/routes_ingest.py` 绑 job_id。随着请求往下走的日志会自动继承。
- 级别语义：INFO = 状态跃迁与业务结果；WARNING = 可自愈的退化 / 重试 / 降级兜底；
  ERROR = 需要人介入；DEBUG = 过程细节、耗时、中间量、逐条打分。
"""
import os
import sys
from loguru import logger

# 默认 INFO。排查问题时用 LOG_LEVEL=DEBUG 起一次即可看到耗时、中间量、逐条打分这些
# 细节，不需要改代码——项目里原本把这些写成 INFO，正是因为当时没有这个开关。
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def _add_context(record) -> None:
    """把 contextualize 绑定的字段渲染成行尾的 ` | k=v k=v`。

    必须由 patcher 无条件写入 `_ctx`：format 里引用一个没绑定的 extra key 会直接抛
    KeyError（loguru 0.7.3 实测），而不是安静地渲染成空。没有绑定任何字段时写空串，
    行尾不留多余的 ` | `。
    """
    extra = {k: v for k, v in record["extra"].items() if k != "_ctx"}
    record["extra"]["_ctx"] = (
        " | " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    )


def configure_logging() -> None:
    logger.remove()     #移除之前配置的所有日志输出方式（handler），避免重复打印。
    # 全局 patcher：每次记录都补上 _ctx，供下面的 format 渲染
    logger.configure(patcher=_add_context)
    # 重新添加一个日志输出方式，这里是把日志输出到 sys.stderr（程序专门用来输出“错误、警告、日志”等信息的一条通道。），并设置日志级别、格式等。
    logger.add(
        sys.stderr,
        level=LOG_LEVEL,     #设置日志输出的最低级别。DEBUG < INFO < WARNING < ERROR < CRITICAL
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        "<dim>{extra[_ctx]}</dim>",
        enqueue=True,   #异步处理日志，让日志写入不太影响主程序。
        backtrace=False,    #发生错误时，不显示更详细的调用栈追踪。
        diagnose=False,   #发生错误时，不显示额外的变量诊断信息。
    )


configure_logging()

__all__ = ["logger"]
