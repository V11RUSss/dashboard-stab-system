# ── Stage 1: Build ───────────────────────────────────────────
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ cmake make ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

# REAL_MODE=ON — сборка с microjetlinux (raw socket, CAP_NET_RAW)
# REAL_MODE=OFF — только sim (обычный UDP)
ARG REAL_MODE=ON
RUN cmake -B build_out \
          -DCMAKE_BUILD_TYPE=Release \
          -DREAL_MODE=${REAL_MODE} \
    && cmake --build build_out --parallel $(nproc)

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /build/build_out/mj_gateway .

# Default env — переопределяется через docker-compose environment
ENV GATEWAY_MODE=real
ENV BACKEND_ADDR=127.0.0.1
ENV BACKEND_PORT=8888
ENV IFACE_NAME=eth0

CMD ["./mj_gateway"]
