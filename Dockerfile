# =============================================================================
# Gridlock Intelligence — Multi-stage production Docker build
#
# Stage 1: Build the React frontend (Node 20)
# Stage 2: Python backend that serves both /api/v1/* and the compiled frontend
#
# Build args:
#   VITE_API_BASE_URL   Leave empty for same-origin deployment (default).
#                       Set to an explicit URL only when frontend and backend
#                       are on different domains.
# =============================================================================

# ── Stage 1: Frontend build ───────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /build

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .

# Empty string = same-origin; API calls go to the same host as the page.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build


# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.10-slim-bullseye

WORKDIR /app

# System dependencies:
#   coinor-cbc   — MILP solver (PuLP backend)
#   libpq-dev    — psycopg2 native client
#   build-essential / gcc / python3-dev — compile scipy/hdbscan wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    libpq-dev \
    build-essential \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ .

# Compiled frontend (served by FastAPI StaticFiles)
COPY --from=frontend-builder /build/dist ./static

# Cloud Run injects PORT; default to 8000 for local docker run
ENV PORT=8000
EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
