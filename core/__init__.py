# 保留此文件：core/ 必须是“常规包”，否则会退化为命名空间包（PEP 420），
# 并被 site-packages 中同名的常规包遮蔽（例如 r2r 会安装一个顶层 core）。
# 一旦被遮蔽，from core.embedding import ... 会解析到第三方包并导入失败。
