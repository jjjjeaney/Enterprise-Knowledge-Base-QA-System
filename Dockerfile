# 单容器：API + ingest worker + MCP server 都在这个镜像里跑。
#
# 这三者是同一棵进程树——app/main.py 的 lifespan 会自动拉起 worker 监督器和 MCP
# server（由 AUTO_START_WORKERS / AUTO_START_MCP 控制）。所以这里只跑 uvicorn，
# 另外两个跟着起来。MCP 监听 127.0.0.1:8001，只在容器内部可达，不需要发布端口。
#
# torch 用 CUDA 版，和开发机保持一致。
#
# 注意 PyPI 上**没有** CPU 版 torch：`pip install torch==2.14.0` 拿到的是 CUDA 13
# 构建（依赖 nvidia-*-cu13 那一串）。CPU 版在 download.pytorch.org/whl/cpu 这个专用源上。
# 这里显式从 cu126 源装，和本机 `pip install torch==2.14.0 --index-url .../cu126`
# 装出来的是同一个东西（本机实测为 2.14.0+cu126）。要出纯 CPU 镜像就换
# --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu。
#
# 基础镜像不用换成 nvidia/cuda——CUDA 运行时会以 nvidia-* 这些 pip 包的形式进来。
# 宿主机侧的前提是 Docker Desktop 的 GPU 透传可用（compose 里做了设备预留）。

FROM python:3.12-slim

# HF_HOME 是模型权重的落盘位置。构建期的预热和运行期的查找都认这一个变量——
# 两边必须一致，否则烤进镜像的权重要么找不到、要么当场重新下一遍。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf

WORKDIR /app

# 依赖单独一层：只改业务代码时不用重装几个 GB 的 torch。
#
# 网络慢就换镜像源，不用改文件：
#   docker build --build-arg PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple -t ekbqas .
ARG PIP_INDEX=https://pypi.org/simple
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126
ARG TORCH_VERSION=2.14.0

# torch 单独一层，而且放在**最前面**（在系统库层之上）。理由有两条：
#   1. requirements.txt 一变，失效的只有它下面的依赖层，几个 GB 的 torch 仍走缓存；
#   2. apt 那层（见下）因为 deb.debian.org 从容器里不稳，参数可能还要再调——它在
#      torch 下面就怎么改都不会连累 torch 重下。
#
# 顺序不能反：torch 必须先装。FlagEmbedding 等包自己也依赖 torch，如果先装其余的，
# pip 会从 PIP_INDEX 拉一个下来，随后那句 `pip install torch==...` 只会看到
# "Requirement already satisfied" 然后什么也不做——镜像里就是个 CUDA 版本不对的 torch，
# 而且不报错。
#
# torchvision 也一起从 cu126 装，别留给下一步从 PyPI 拉：PyPI 的 torchvision 0.29.0 是
# 配 CUDA 13 的构建，而 docling[standard] 会带进来 torchvision。让它在下一步装，拿到的
# 是 cu13 版 torchvision 配上 cu126 的 torch，两个的 CUDA ABI 不一致，torchvision 的
# CUDA op 一跑就可能报错。这里装 0.29.0+cu126，和开发机（实测也是 0.29.0+cu126）一致。
#（版本号本身不会触发重装 torch——pip 对 `==2.14.0` 会忽略本地版本段，`2.14.0+cu126`
# 是满足的；真正的问题是 ABI，不是版本。）
RUN pip install -i "$TORCH_INDEX" "torch==$TORCH_VERSION" "torchvision==0.29.0"

# 运行期要的系统库。libgl1 / libglib2.0-0 是 OpenCV 系的依赖（docling 的 OCR 链路会用到）
# ——docling[standard] 会经 rapidocr 拉进来 opencv-python（非 headless），它要 libGL.so.1，
# 少了这两个包会在 import cv2 时当场炸。
#
# 下面这串参数是实测调出来的，别随手简化：deb.debian.org 从容器里访问很不稳（实测
# 87.9 kB/s，40 个包里 libllvm19 / mesa-libgallium 反复报 500 / unexpected EOF），
# apt 默认 3 次重试不够，整层会直接失败。Retries=10 + http::Timeout=30 + --fix-missing
# 是实测能过组合。注意重试参数**必须同时加在 update 和 install 上**——失败最多的其实
# 是 update 抓索引那一步，而原来只在 install 上设了。
#
# 代价是这层可能跑十几分钟。它待在 torch 层**下面**是有意的：以后再动这里的参数，
# 不会连带重下几个 GB 的 torch。
RUN apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=30 --fix-missing install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 其余依赖单独一层：只改这一层，上面的 torch 不用重下。
#
# --timeout / --retries 是必须的，和上面 apt 那层同一个道理：这条网络路径到境外源
# 只有 0.7–1.8 MB/s，pip 默认的 15 秒超时 + 5 次重试不够用。不够用时的报错很有
# 误导性——它不说"超时"，而是说
#   ERROR: Could not find a version that satisfies the requirement scipy<2.0.0,>=1.6.0
#   ERROR: No matching distribution found for scipy<2.0.0,>=1.6.0
# 看着像版本冲突，其实只是 5 次读超时耗尽后 pip 的笼统说法，scipy 的版本没问题。
# 别被它骗着去改 requirements.txt。
#
# cache mount 也是必须的，原因是一次踩坑：这条链路会**零星把包下坏**，happened 在
# jieba3k 上——pip 拿文件和索引公布的 sha256 一比，对不上就终止，而且哈希不匹配
# **不算可重试错误**，整层直接失败。没有缓存时，这种偶发失败要从头重下全部依赖
# （实测约 20 分钟），代价高得离谱；挂上 cache mount 后重跑只补缺的那几个，一两分钟。
# 缓存落在构建缓存里、不进镜像层，不会让镜像变大。
#
# `unset PIP_NO_CACHE_DIR` 这一句不能省：文件头的 ENV 块里有 PIP_NO_CACHE_DIR=1，
# 会让 pip 完全不写缓存，cache mount 就成了摆设。而那个 ENV 在 torch 层**上面**，
# 改它会让 58 分钟的 torch 层作废，所以只能在这里局部 unset 掉。
#（试过 `PIP_NO_CACHE_DIR=0` 内联覆盖，实测无效——缓存依然是空的。）
#
# 万一哪天报的还是同一个包的哈希错误，说明坏文件被缓存住了，清一下构建缓存再跑：
#   docker buildx prune --filter type=exec.cachemount -f
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    unset PIP_NO_CACHE_DIR \
    && grep -v -E '^torch==' requirements.txt > /tmp/requirements-no-torch.txt \
    && pip install -i "$PIP_INDEX" --timeout 120 --retries 10 -r /tmp/requirements-no-torch.txt

# 重排模型本地路径，指向上面 `COPY . .` 带进来的目录（见 warm 那段的说明）。单独一行
# 且放在 pip 层之后：ENV 一旦变化，就会让**它上面的**几 GB torch/deps 层一起失效重装，
# 所以不能并进文件头那个 ENV 块——那等于重下几十分钟。放这里只失效 COPY 和 warm 两步。
ENV RERANKER_MODEL_PATH=/app/models/bge-reranker-v2-m3

COPY . .

# 构建期预热模型权重。
#
# 必须真正跑一次推理，不能只 import：这些权重是第一次解析文档 / 第一次打分时才
# 去下载的，只 import 什么都下不到，等容器跑起来第一次上传文档才会去拉——那时
# 镜像已经交付出去，很可能没网，直接失败。
#
# 唯一的例外是重排模型：bge-reranker-v2-m3 在 HF 上走 Xet 存储（us.aws.cdn.hf.co），
# 国内连不上，构建期预热时一跑就失败。所以它不在这里下，而是上面 `COPY . .` 时
# 把本地 `models/bge-reranker-v2-m3/` 整个带进镜像，RERANKER_MODEL_PATH 直接指向
# 那个本地目录。warm_models 里 `_warm_reranker` 加载的是本地路径，不再联网。
#
# 绕一圈 cache mount 是为了改代码时不必重下几个 GB：如果把 HF_HOME 直接指到镜像
# 层里，这一层会因为上面 `COPY . .` 的内容变化整体失效，每次构建都重下一遍。
# 挂在构建缓存上，重跑时 cp 走本地磁盘，不碰网络。
RUN --mount=type=cache,target=/hf-cache \
    HF_HOME=/hf-cache python scripts/warm_models.py \
    && mkdir -p /models/hf \
    && cp -a /hf-cache/. /models/hf/

EXPOSE 8000

# 用 exec 形式，让 uvicorn 成为 PID 1 直接收到 SIGTERM —— 它要跑 lifespan 的收尾，
# 去停 worker 子进程和 MCP。配合 compose 里的 init: true 回收子进程、stop_grace_period
# 给足收尾时间。--host 0.0.0.0 是必须的，绑 127.0.0.1 的话端口发布不出去。
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
