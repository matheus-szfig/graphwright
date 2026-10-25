# Dev image for the graph builder UI (Vite dev server, base /builder/).
FROM node:26-bookworm-slim

ENV NODE_ENV=development \
    CI=true \
    PATH="/app/node_modules/.bin:${PATH}"

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY . .

EXPOSE 5174

# vite.config.ts sets base "/builder/", host true, port 5174.
CMD ["npm", "run", "dev"]
