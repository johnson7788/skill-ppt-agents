FROM node:20

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

COPY . .

EXPOSE 3686
# 远程 IP 访问时关掉 HMR websocket，避免连不上 localhost。后端地址由 BACKEND_URL 注入。
ENV DISABLE_HMR=true

CMD ["npm", "run", "dev"]
