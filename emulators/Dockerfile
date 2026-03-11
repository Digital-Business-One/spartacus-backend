# Base: Java 21 (necessário para firebase-tools >= 13) + Node 20
FROM eclipse-temurin:21-jre-jammy

RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
