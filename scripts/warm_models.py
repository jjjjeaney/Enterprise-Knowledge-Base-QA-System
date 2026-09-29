"""把 Docling 和 rerank 的模型权重下载到 HF_HOME 下。构建镜像时跑一次用。

    HF_HOME=/models/hf python scripts/warm_models.py

**必须真正跑一次推理**，不能只 import：这些权重是第一次解析文档 / 第一次打分时
才去下载的，只 import 的话这里什么都下不到，等容器跑起来第一次上传文档才会去拉
——那时镜像已经交付出去，很可能没网，直接失败。

PDF 是现用现生成的，不读仓库里的文件：构建上下文里未必带测试文档，让预热脚本
依赖某个样例文件很脆。
"""
import os
import sys
import tempfile

# 直接 `python scripts/warm_models.py` 运行时，sys.path[0] 是 scripts/ 而不是仓库根，
# 下面的 `from app...` / `from core...` 会 ImportError。和 app/main.py 一样，把仓库根
# 显式插进 sys.path，这样这个脚本怎么调都能跑（也能 `python -m scripts.warm_models`）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.logging_config import logger
from core.config import RERANKER_MODEL_PATH
from core.reranker import get_reranker
from ingestion import get_docling_converter


def _minimal_pdf() -> bytes:
    """生成一份结构完整的一页 PDF（xref 偏移是算出来的，不是占位符）。

    手写 PDF 容易在 xref 上翻车，而解析失败会让预热静默地什么都没下到——
    那正是这个脚本要避免的情况，所以这里老老实实把偏移算对。
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 20 100 Td (model warmup) Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def _warm_docling() -> None:
    converter = get_docling_converter()

    # 走文件路径而不是内存流：ingestion/process.py 的 load_doc 交给 convert 的也是
    # 落盘路径，这里保持同一条路径，免得预热通过了、真实解析却走到另一条分支。
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(_minimal_pdf())
        path = f.name

    try:
        document = converter.convert(path).document
    finally:
        os.unlink(path)

    logger.info("docling 权重就绪 text_len={}", len(document.export_to_markdown()))


def _warm_reranker() -> None:
    reranker = get_reranker()
    score = reranker.compute_score([["预热查询", "预热文档"]], normalize=True)
    logger.info("rerank 权重就绪 path={} score={}", RERANKER_MODEL_PATH, score)


def main() -> int:
    logger.info("开始预热模型权重，落盘位置由 HF_HOME 决定")
    try:
        _warm_docling()
        _warm_reranker()
    except Exception as e:
        # 整体的 try：Docling 的权重先落盘，再轮到 rerank 报错，构建失败时也能
        # 从日志看出是下了一部分还是一点都没下。
        logger.exception("模型预热失败 error={}", e)
        return 1
    logger.info("模型预热完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
