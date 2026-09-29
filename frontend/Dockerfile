# 前端单独一个镜像：node 负责构建，nginx 只负责发静态文件。
#
# 为什么用 `-slim`（Debian/glibc）而不是 `alpine`（musl）：package-lock.json 是在
# Windows 上生成的，里面锁的是 @rollup/rollup-win32-x64-msvc 这类**平台相关的可选依赖**。
# 在 Linux 里 npm ci 解析这组依赖本来就有已知缺陷，musl 那一套更容易撞上
# `Cannot find module @rollup/rollup-linux-x64-musl`。glibc 是最稳妥的路。
# node 大版本对齐本机（宿主 node --version = v24.16.0），免得出现"本机能构建、容器里不行"。
FROM node:24-slim AS build

WORKDIR /app

# 先只拷两个 manifest 再装依赖：改源码时这一层命中缓存，不会重装 node_modules。
# 必须用 npm ci 而不是 npm install —— 有 package-lock.json 在，ci 严格按锁文件装，
# 构建结果可复现。
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- 运行阶段 ----
#
# 这里放自定义 nginx.conf：路由是 hash 模式（src/router/index.js 的 createWebHashHistory），
# 静态那部分本来用默认配置就够（浏览器只请求 `/` 和静态资源，用不上 try_files 回退），
# 但 /api 反代必须自己写，所以要覆盖掉 conf.d/default.conf。
#
# 用 `nginx:latest` 而不是 `nginx:alpine`：本机已经有 latest（238MB）、没有 alpine，
# 而 Docker Hub 在这条网络下很不可靠（实测 IPv6 认证超时 + 层下载中途 unexpected EOF）。
# 少一个要拉的镜像就少一处会断的地方。代价是镜像大 ~190MB，对已经跑着 17.6GB app 镜像
# 的机器来说无所谓。想换小的就把这行改成 nginx:alpine 并确保能拉到。
FROM nginx:latest

# ★ 这份配置必须在**运行阶段**拷。放到构建阶段（node 那边）会被连同那一层一起丢弃，
#   最终镜像里只剩 nginx 的默认配置——反代和 client_max_body_size / proxy_read_timeout
#   全都不生效，而且不报任何错。
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

# 容器内听 80（nginx 默认），compose 里发布成宿主机的 5173。
# 5173 是**唯一对外入口**：静态文件和 /api 都由这个 nginx 提供，浏览器只跟它说话。
#
# 因为前后端同源，后端那套 CORS 白名单（app/main.py 的 allow_origins）已经用不上了，
# 也不再限制必须用 localhost —— 127.0.0.1:5173 一样能打开。
#
# 注意 app 不再发布 8000 端口（只留单一源），所以宿主机 `npm run dev` 连不到后端、
# 已经不可用，前端开发只能走容器。想恢复本机开发：把 app 的 8000 发布回来，
# 再给 vite 配上 server.proxy。
