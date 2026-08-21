FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS build

WORKDIR /build/frontend2

COPY frontend2/package.json frontend2/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --fund=false

COPY frontend2/ ./
COPY config/privacy_membership_notice.json /build/config/privacy_membership_notice.json

RUN npm run build

FROM nginx:1.30.4-alpine@sha256:97d490c12ba55b4946b01546d1c3ed324e8d41ab1c9fcb2a616aa470620e5b46

RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /build/frontend2/dist /usr/share/nginx/html

USER nginx

EXPOSE 8080
