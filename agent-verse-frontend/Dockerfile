# Multi-stage React build
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --frozen-lockfile
COPY . .
ARG VITE_API_URL=http://localhost:8000
ARG VITE_GRAFANA_URL=http://localhost:3001
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY infra/nginx-frontend.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=10s CMD wget -qO- http://localhost/ || exit 1
