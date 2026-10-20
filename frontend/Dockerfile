# node:22-alpine (Node 22.23.3). Digest of the multi-arch index, looked up on 2026-10-15.
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build

WORKDIR /app

# Dependencies first, so a source edit does not reinstall them.
COPY package.json package-lock.json ./
RUN npm ci

# The production configuration is the default one in angular.json.
COPY . .
RUN npx ng build

# nginx:stable-alpine (nginx 1.30.6). Digest of the multi-arch index, looked up on 2026-10-15.
FROM nginx:stable-alpine@sha256:ed84151ed2d47ef93c0939eb9b9754c6990db6092cd2081e11988a47a83d47ea

# The stock nginx.conf includes conf.d/*.conf inside its http block.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/car-advisor/browser /usr/share/nginx/html

EXPOSE 8080
