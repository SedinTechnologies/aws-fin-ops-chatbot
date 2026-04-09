FROM node:25.2.0-alpine3.21

WORKDIR /app

RUN apk add --no-cache openssl

# Install Prisma CLI globally
RUN npm install -g prisma@6.19.0

ENTRYPOINT [ "npx", "prisma", "migrate", "deploy" ]
