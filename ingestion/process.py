import asyncio
import base64
import io
import os
import sys
import time

from loguru import logger
from docling_core.types.doc import PictureItem, TableItem
from langchain_core.messages import HumanMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm import get_deepseek_vision_llm, llm_invoke_gateway
from ingestion import get_docling_converter
# ingestion/process.py
from core.config import VISION_CONCURRENCY
from core.prompts import PROCESS_PICTURE_PROMPT

async def _limited_picture(image, sem):
    async with sem:                       # 令牌在此取，编码和请求都在闸门内
        return await load_picture(image)

async def load_picture(image):
    """
    直接处理内存中的 PIL.Image，不创建临时文件
    """
    llm = get_deepseek_vision_llm()
    # PIL Image -> PNG bytes
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    # PNG bytes -> Base64
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    messages=[
            HumanMessage(
                content_blocks=[
                    {
                        "type": "text",
                        "text": PROCESS_PICTURE_PROMPT,
                    },
                    {
                        "type": "image",
                        "base64": b64,
                        "mime_type": "image/png",
                    },
                ]
            )
        ]
    llm=get_deepseek_vision_llm()
    response=await llm_invoke_gateway(llm, messages)
    return response.content


async def load_doc(path: str):
    converter = get_docling_converter()
    sem = asyncio.Semaphore(VISION_CONCURRENCY)
    # Docling 是同步阻塞的 CPU 密集调用。直接 await 会把事件循环整个堵住，
    # worker 的续租心跳因此停摆，租约到期后这份文件会被重复领取。
    # 挪到线程里跑：GIL 不让它真的并行（并行度靠多起 worker 进程），
    # 但事件循环能继续调度，心跳才发得出去。
    doc_result = await asyncio.to_thread(converter.convert, path)
    document = doc_result.document
    items = list(document.iterate_items())

    parts = []
    picture_tasks = []

    for index, (item, level) in enumerate(items):

        # 图片
        if isinstance(item, PictureItem):

            image = item.get_image(document)

            if image is None:
                continue

            # 直接把 PIL Image 传给异步任务
            task = asyncio.create_task(_limited_picture(image, sem))

            picture_tasks.append((index, task))

            # 先占一个位置
            parts.append(
                (index, "picture", task)
            )

        # 表格
        elif isinstance(item, TableItem):

            table_text = item.export_to_markdown()

            parts.append(
                (
                    index,
                    "text",
                    "\n<-- Table -->\n"
                    + table_text
                    + "\n<-- Table -->\n",
                )
            )

        # 普通文本
        else:

            parts.append(
                (
                    index,
                    "text",
                    item.text,
                )
            )

    # 等待所有图片识别任务完成
    logger.debug("等待图片识别任务完成 picture_count={}", len(picture_tasks))

    results = await asyncio.gather(
        *(task for _, task in picture_tasks)
    )

    # task -> 识别结果
    task_to_text = {
        task: text
        for (_, task), text in zip(picture_tasks, results)
    }

    result_text = ""

    # 按照原文档顺序重新组合
    for index, kind, value in parts:

        if kind == "text":

            result_text += value

        elif kind == "picture":

            text = task_to_text[value]

            result_text += (
                "\n\n<-- image -->\n"
                + text
                + "\n<-- image -->\n\n"
            )

    return result_text


async def split_doc(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    cleaned_text = text.replace("", "")

    return splitter.split_text(cleaned_text)


async def process_doc(
    path: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
):
    start = time.perf_counter()

    suffix = os.path.splitext(path)[1].lower()
    is_image = suffix in [".jpg", ".png", ".jpeg"]
    logger.debug("解析文档 path={} kind={}", path, "image" if is_image else "document")

    if is_image:

        # 这里仍然可以让 load_picture 接收文件路径之外的输入
        # 如果是用户直接上传的图片，可以在这里用 PIL.Image.open()
        from PIL import Image

        image = Image.open(path)

        doc_content = await load_picture(image)

    else:

        doc_content = await load_doc(path)

    chunks = await split_doc(
        doc_content,
        chunk_size,
        chunk_overlap,
    )

    logger.info(
        "文档解析完成 path={} chunks={} chunk_size={} chunk_overlap={} elapsed_ms={:.0f}",
        path,
        len(chunks),
        chunk_size,
        chunk_overlap,
        (time.perf_counter() - start) * 1000,
    )

    return chunks




async def main():
    """手动跑一遍解析，用来单独调 process_doc——不经过 worker、数据库和向量库。

        python -m ingestion.process <文件路径>
    """
    if len(sys.argv) < 2:
        print("用法: python -m ingestion.process <文件路径>")
        raise SystemExit(2)

    result = await process_doc(sys.argv[1])
    for chunk in result:
        print(chunk + "\n\n")


if __name__ == '__main__':
    asyncio.run(main())


