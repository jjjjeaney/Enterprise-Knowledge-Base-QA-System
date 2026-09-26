"""把 ingest worker 起多份，并在它们意外退出时自动拉起来。

    python run_workers.py 3      # 起 3 个
    python run_workers.py        # 按 INGEST_WORKERS（默认 2）

这**只是个进程管理器**：它自己不领活、不连数据库、不参与任何调度。它做的就是
反复执行 `python -m app.worker`，盯着这些子进程，谁死了重启谁。真正的并发分发
由数据库的 FOR UPDATE SKIP LOCKED 完成，跟这个脚本无关——所以杀掉这个脚本不会
丢数据，正在处理的那条记录状态仍是 running，租约到期后会被其它 worker 自动捡回。

已知限制：如果这个脚本自己被强杀（而不是 Ctrl+C），子进程会变成孤儿继续跑。
发现 worker 数量对不上时，用 `tasklist | findstr python` 看一眼再手工收掉。
"""
import subprocess
import sys
import threading
import time

from app.logging_config import logger
from core.config import INGEST_WORKERS

# 用 sys.executable 而不是字面量 "python"：本机裸 python 是个 Store 占位程序，
# 调它会静默失败。这样能保证子进程和父进程用的是同一个解释器。
WORKER_CMD = [sys.executable, "-m", "app.worker"]

# 子进程刚启动就退出，几乎一定是启动就失败（例如 Milvus 连不上）。这种情况
# 立刻重启会变成热循环把日志刷爆，所以要退避。
MIN_RESTART_DELAY = 2.0
MAX_RESTART_DELAY = 60.0
# 跑够这么久才退出，算"正常跑过一阵的意外退出"（比如原生代码段错误），退避清零
HEALTHY_RUN_SECONDS = 30.0
# Ctrl+C 之后等子进程自己收尾的宽限时间，超时就强杀
SHUTDOWN_GRACE_SECONDS = 10.0


class _Slot:
    """一个 worker 槽位。进程会死，槽位一直在，所以槽位数就是并发度。"""

    def __init__(self, index: int) -> None:
        self.index = index
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0
        self.fail_streak = 0
        self.restart_at = 0.0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        # 不捕获 stdout/stderr：worker 的日志直接打到同一个终端，能看到全局
        self.proc = subprocess.Popen(WORKER_CMD)
        self.started_at = time.time()
        logger.bind(slot=self.index).info("worker 启动 pid={}", self.proc.pid)

    def note_death(self) -> None:
        """子进程没了，记一笔，并算出下次最早什么时候可以重启。"""
        pid = self.proc.pid
        code = self.proc.returncode
        ran_for = time.time() - self.started_at
        self.proc = None
        log = logger.bind(slot=self.index)

        if ran_for >= HEALTHY_RUN_SECONDS:
            self.fail_streak = 0
            delay = 0.0
            log.warning(
                "worker 运行 {:.0f}s 后退出，视为意外死亡，立即重启 pid={} returncode={}",
                ran_for,
                pid,
                code,
            )
        else:
            self.fail_streak += 1
            delay = min(
                MAX_RESTART_DELAY,
                MIN_RESTART_DELAY * 2 ** (self.fail_streak - 1),
            )
            log.error(
                "worker 启动后 {:.1f}s 即退出，疑似启动失败 "
                "pid={} returncode={} fail_streak={} retry_in_s={:.0f}",
                ran_for,
                pid,
                code,
                self.fail_streak,
                delay,
            )
        self.restart_at = time.time() + delay


def _shutdown(slots: list[_Slot]) -> None:
    """停掉所有子进程。

    Ctrl+C 在 Windows 上会发给同一个控制台里的所有进程，所以 worker 自己多半
    已经收到并优雅退出了（它们会打一行"收到中断信号"）。这里只是兜底。
    """
    deadline = time.time() + SHUTDOWN_GRACE_SECONDS
    while time.time() < deadline and any(s.alive() for s in slots):
        time.sleep(0.2)

    for slot in slots:
        if slot.alive():
            logger.bind(slot=slot.index).warning(
                "worker 未在宽限期内自行退出，强制结束 pid={}", slot.proc.pid
            )
            slot.proc.terminate()

    for slot in slots:
        if slot.proc is None:
            continue
        try:
            slot.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            slot.proc.kill()

    logger.info("全部 worker 已停止")


def _parse_worker_count(argv: list[str]) -> int:
    if len(argv) <= 1:
        return INGEST_WORKERS
    try:
        n = int(argv[1])
    except ValueError:
        logger.error("worker 数量必须是整数 arg={!r}", argv[1])
        raise SystemExit(2)
    if n < 1:
        logger.error("worker 数量至少为 1 count={}", n)
        raise SystemExit(2)
    return n


def main(argv: list[str], stop_event: threading.Event | None = None) -> int:
    n = _parse_worker_count(argv)
    logger.info("启动 ingest worker 进程 count={}（Ctrl+C 停止，子进程意外退出会自动重启）", n)

    slots = [_Slot(i) for i in range(n)]
    try:
        # stop_event 为 None 时（CLI 直接运行）永真，靠 Ctrl+C 抛 KeyboardInterrupt 退出；
        # 由 API 进程以线程方式调用时，置 stop_event 让循环干净退出并走 finally 收尾。
        while stop_event is None or not stop_event.is_set():
            for slot in slots:
                with logger.contextualize(slot=slot.index):
                    if slot.alive():
                        continue
                    if slot.proc is not None:      # 刚发现它死了
                        slot.note_death()
                    if time.time() >= slot.restart_at:
                        slot.start()
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，等待 worker 退出")
    finally:
        _shutdown(slots)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))


