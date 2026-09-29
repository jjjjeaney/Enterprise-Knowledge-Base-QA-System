"""初始化数据层：PostgreSQL 表结构 + Milvus collection。

    python -m db.init_db

线上那套表原本是外部建好、db/models.py 反过来对齐它的（README「已知限制」里
那条「没有迁移」说的就是这件事）。这个脚本让一个全新的环境只靠仓库内容就能建
起来，容器部署时用它替代「手工准备一个和线上一样的库」。

幂等：两边的对象都存在就整块跳过，可以反复执行。但它**只创建缺失的对象，
不改动已存在的**——已有的表/collection 和仓库定义不一致时这里不会报错也不会
去对齐，schema 变更仍然要靠手工 DDL 或迁移工具。
"""
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.logging_config import logger
from core.config import MILVUS_COLLECTION, MILVUS_DB, POSTGRESQL_URL
from db import Base, get_milvus_client

# 导入即把全部模型注册到 Base.metadata。少了这句 create_all 什么都建不出来，
# 而且不会报错——metadata 是空的，它就安静地什么都不做。
import db.models  # noqa: F401

_TABLES_SQL = text(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
)

# --- Milvus collection 的定义 ---
#
# 线上那个 Documents 只**声明**了两个字段：主键 id 和向量 vector。content /
# metadata / created_at 全是动态字段，存在隐藏的 $meta 里——这是 MilvusClient
# 快速建表（quick setup）的产物，enable_dynamic_field 默认开着。
#
# 别改成显式声明这几个字段：显式 schema 要给 content 定 max_length，而切块长度
# 是浮动的，定小了会被静默截断。动态字段没有这个限制。
#
# 下面几项是照着线上实际定义核对过的（字段、索引类型、metric 都逐项比对过），
# 不是按默认值猜的——换掉 metric 或改成显式 schema 都会让检索结果和线上不一致。
_VECTOR_DIM = 3072      # text-embedding-3-large 的输出维度
_ID_MAX_LENGTH = 36     # chunk id 是 uuid5 字符串，正好 36 个字符


async def _tables(conn) -> set[str]:
    return {row[0] for row in await conn.execute(_TABLES_SQL)}


async def _init_postgres() -> bool:
    expected = set(Base.metadata.tables)

    # 不复用 db/__init__.py 那个 engine：它建的时候带 echo=True，在常驻服务里有用，
    # 但建表脚本的 checkfirst 反射查询会把日志刷满屏。这里单独建一个安静的。
    # 连的仍是同一个 POSTGRESQL_URL，所以验证到的还是真实那套连接配置。
    engine = create_async_engine(POSTGRESQL_URL)

    try:
        async with engine.begin() as conn:
            before = await _tables(conn)
            await conn.run_sync(Base.metadata.create_all)
            after = await _tables(conn)
    except Exception as e:
        logger.error("建表失败，请检查 POSTGRESQL_URL 与数据库是否可达 error={}", e)
        return False
    finally:
        await engine.dispose()

    created = sorted(after - before)
    skipped = sorted(before & after)
    logger.info("PostgreSQL 建表完成 新建={} 已存在跳过={}", len(created), len(skipped))
    for name in created:
        logger.info("  新建 {}", name)

    missing = sorted(expected - after)
    if missing:
        # 走到这里几乎只有一个原因：某个模型没被 import 到，于是没注册进 metadata。
        logger.error("以下模型未在库中建出，检查它们是否被 import missing={}", missing)
        return False

    return True


def _init_milvus() -> bool:
    try:
        client = get_milvus_client()

        if MILVUS_DB not in client.list_databases():
            client.create_database(MILVUS_DB)
            logger.info("Milvus database 已创建 db={}", MILVUS_DB)

        client.use_database(MILVUS_DB)

        if MILVUS_COLLECTION in client.list_collections():
            logger.info(
                "Milvus collection 已存在，跳过 collection={} db={}",
                MILVUS_COLLECTION,
                MILVUS_DB,
            )
            return True

        client.create_collection(
            collection_name=MILVUS_COLLECTION,
            dimension=_VECTOR_DIM,
            primary_field_name="id",
            id_type="string",
            max_length=_ID_MAX_LENGTH,
            vector_field_name="vector",
            metric_type="COSINE",
            auto_id=False,
        )
        logger.info(
            "Milvus collection 已创建 collection={} db={} dim={}",
            MILVUS_COLLECTION,
            MILVUS_DB,
            _VECTOR_DIM,
        )
        return True
    except Exception as e:
        logger.error("建 collection 失败，请检查 MILVUS_URI 与 Milvus 是否可达 error={}", e)
        return False


async def main() -> int:
    ok = await _init_postgres()
    # 两边互不依赖，一个失败不影响另一个继续建——反正幂等，修好再跑一遍即可。
    ok = _init_milvus() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
