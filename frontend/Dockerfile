FROM node:24.13.0-alpine3.23@sha256:cd6fb7efa6490f039f3471a189214d5f548c11df1ff9e5b181aa49e22c14383e AS builder

RUN npm install --global pnpm@10.15.1
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.28.0-alpine3.21@sha256:c97ff0bf7cbae369953c6da1232ec14ad9f971d66360c5698db0856a4cd657a0 AS runtime

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=builder --chown=101:101 /app/dist /usr/share/nginx/html

USER 101
EXPOSE 8080
