FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS build

WORKDIR /build/ops-console

COPY ops-console/package.json ops-console/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --fund=false

COPY ops-console/ ./

ENV VITE_API_BASE_URL="" \
    VITE_OPS_BASE_PATH="/" \
    VITE_OPS_CSRF_COOKIE_NAME="mooncen_ops_csrf"

RUN npm run build

FROM scratch
COPY --from=build /build/ops-console/dist /
