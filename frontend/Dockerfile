# 1단계: Next.js 정적 파일 빌드
FROM node:24-alpine AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# 화면의 기본 기준일은 빌드 때 박는다.
# 프런트는 정적 내보내기라 `next start` 가 없다. 런타임 env 가 안 닿는다.
# 화면 실행 ID 는 빌드에 박지 않는다 — 화면이 백엔드(`GET /api/console/shown-run`)에서 받는다.
ARG NEXT_PUBLIC_AS_OF=2026-09-17
ENV NEXT_PUBLIC_AS_OF=$NEXT_PUBLIC_AS_OF

RUN npm run build

# 2단계: 빌드된 정적 파일을 Nginx로 제공
FROM nginx:1.29-alpine

COPY --from=builder /app/out /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

COPY nginx/proxy_params /etc/nginx/proxy_params
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
