FROM node:26.13.1-trixie AS base
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.js start.sh start-exec.sh ./
RUN chmod +x start.sh start-exec.sh

FROM base AS exec-form
CMD ["node", "server.js"]

FROM base AS shell-form
CMD node server.js

FROM base AS script
CMD ["./start.sh"]

FROM base AS script-exec
CMD ["./start-exec.sh"]
