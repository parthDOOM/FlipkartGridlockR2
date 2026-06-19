#!/usr/bin/env bash
# =============================================================================
# Gridlock Intelligence — GCP Deployment Script
#
# What this does, in order:
#   1. Validates prerequisites (gcloud, docker)
#   2. Creates Artifact Registry repository
#   3. Creates Cloud SQL PostgreSQL instance with PostGIS
#   4. Creates the database, user, and stores the URL as a Secret Manager secret
#   5. Builds the Docker image (multi-stage: React + FastAPI)
#   6. Pushes to Artifact Registry
#   7. Deploys to Cloud Run (reads DATABASE_URL from Secret Manager)
#   8. Uploads the violation CSV to Cloud Storage and runs data ingestion
#   9. Seeds sample events
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Or with overrides:
#   PROJECT_ID=my-project REGION=asia-south1 ./deploy.sh
# =============================================================================

set -euo pipefail

# ── Configuration (override via env vars) ─────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-gridlock-intelligence}"
REPO_NAME="${REPO_NAME:-gridlock}"
DB_INSTANCE="${DB_INSTANCE:-gridlock-db}"
DB_NAME="${DB_NAME:-congestion_db}"
DB_USER="${DB_USER:-gridlock_user}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 24)}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-gridlock-data}"
SECRET_NAME="gridlock-db-url"
CSV_FILE="${CSV_FILE:-jan to may police violation_anonymized791b166.csv}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"

# ── Colors ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Preflight checks ───────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Gridlock Intelligence — GCP Deployment"
echo "  Project : ${PROJECT_ID}"
echo "  Region  : ${REGION}"
echo "  Service : ${SERVICE_NAME}"
echo "============================================================"
echo ""

command -v gcloud >/dev/null 2>&1 || error "gcloud CLI not found. Install from https://cloud.google.com/sdk"
command -v docker  >/dev/null 2>&1 || error "Docker not found. Install from https://docs.docker.com/get-docker/"

[[ -z "${PROJECT_ID}" ]] && error "PROJECT_ID not set. Run: gcloud config set project YOUR_PROJECT_ID"

gcloud config set project "${PROJECT_ID}" --quiet

# ── Enable required APIs ───────────────────────────────────────────────────────
info "Enabling GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    --quiet
success "APIs enabled"

# ── Artifact Registry ─────────────────────────────────────────────────────────
info "Creating Artifact Registry repository..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --quiet 2>/dev/null; then
    gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --description="Gridlock Intelligence Docker images" \
        --quiet
    success "Repository '${REPO_NAME}' created"
else
    success "Repository '${REPO_NAME}' already exists"
fi
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# ── Cloud SQL ─────────────────────────────────────────────────────────────────
info "Checking Cloud SQL instance '${DB_INSTANCE}'..."
if ! gcloud sql instances describe "${DB_INSTANCE}" --quiet 2>/dev/null; then
    info "Creating Cloud SQL instance (PostgreSQL 14 + PostGIS). This takes ~5 minutes..."
    gcloud sql instances create "${DB_INSTANCE}" \
        --database-version=POSTGRES_14 \
        --tier=db-f1-micro \
        --region="${REGION}" \
        --storage-type=SSD \
        --storage-size=20GB \
        --storage-auto-increase \
        --database-flags=cloudsql.enable_pgaudit=off \
        --no-assign-ip \
        --quiet
    success "Cloud SQL instance '${DB_INSTANCE}' created"
else
    success "Cloud SQL instance '${DB_INSTANCE}' already exists"
fi

# Enable PostGIS extension (Cloud SQL flag required for POSTGRES_14)
gcloud sql instances patch "${DB_INSTANCE}" \
    --database-flags=cloudsql.enable_pgaudit=off \
    --quiet 2>/dev/null || true

info "Creating database and user..."
gcloud sql databases create "${DB_NAME}" --instance="${DB_INSTANCE}" --quiet 2>/dev/null || \
    warn "Database '${DB_NAME}' already exists"

gcloud sql users create "${DB_USER}" \
    --instance="${DB_INSTANCE}" \
    --password="${DB_PASSWORD}" \
    --quiet 2>/dev/null || \
    warn "User '${DB_USER}' already exists — password unchanged"

# Get the Cloud SQL connection name (PROJECT:REGION:INSTANCE)
CONN_NAME=$(gcloud sql instances describe "${DB_INSTANCE}" --format="value(connectionName)")
success "Cloud SQL connection name: ${CONN_NAME}"

# Build the DATABASE_URL for Cloud Run (Unix socket connection)
DB_URL="postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CONN_NAME}"

# ── Secret Manager ────────────────────────────────────────────────────────────
info "Storing DATABASE_URL in Secret Manager..."
if gcloud secrets describe "${SECRET_NAME}" --quiet 2>/dev/null; then
    echo -n "${DB_URL}" | gcloud secrets versions add "${SECRET_NAME}" --data-file=- --quiet
    success "Secret '${SECRET_NAME}' updated"
else
    echo -n "${DB_URL}" | gcloud secrets create "${SECRET_NAME}" --data-file=- --quiet
    success "Secret '${SECRET_NAME}' created"
fi

# Grant Cloud Run service account access to the secret
SERVICE_ACCOUNT="${PROJECT_ID}@appspot.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet 2>/dev/null || true

# ── Docker build & push ───────────────────────────────────────────────────────
info "Building Docker image..."
docker build \
    --tag "${IMAGE}" \
    --build-arg VITE_API_BASE_URL="" \
    --file Dockerfile \
    .
success "Docker image built: ${IMAGE}"

info "Pushing image to Artifact Registry..."
docker push "${IMAGE}"
success "Image pushed"

# ── Cloud Run deployment ───────────────────────────────────────────────────────
info "Deploying to Cloud Run..."

# Grant Cloud Run SA access to Cloud SQL
CR_SA="$(gcloud run services describe ${SERVICE_NAME} --region=${REGION} --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || echo "")"
if [[ -n "${CR_SA}" ]]; then
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${CR_SA}" \
        --role="roles/cloudsql.client" \
        --quiet 2>/dev/null || true
fi

gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE}" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --memory=2Gi \
    --cpu=2 \
    --min-instances=0 \
    --max-instances=3 \
    --concurrency=80 \
    --timeout=300 \
    --add-cloudsql-instances="${CONN_NAME}" \
    --set-secrets="DATABASE_URL=${SECRET_NAME}:latest" \
    --set-env-vars="ALLOWED_ORIGINS=*" \
    --quiet

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --format="value(status.url)")
success "Cloud Run deployed: ${SERVICE_URL}"

# ── Data ingestion ─────────────────────────────────────────────────────────────
echo ""
info "Starting data ingestion..."

if [[ -f "${CSV_FILE}" ]]; then
    # Upload CSV to Cloud Storage for ingestion
    info "Uploading violation CSV to Cloud Storage bucket '${BUCKET_NAME}'..."
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --location="${REGION}" --quiet 2>/dev/null || true

    gcloud storage cp "${CSV_FILE}" "gs://${BUCKET_NAME}/violations.csv" --quiet
    success "CSV uploaded to gs://${BUCKET_NAME}/violations.csv"

    info "Running data ingestion against Cloud SQL via Cloud SQL Auth Proxy..."
    info "This requires Cloud SQL Auth Proxy. Starting it now..."

    # Download Cloud SQL Auth Proxy if not present
    if ! command -v cloud-sql-proxy >/dev/null 2>&1; then
        warn "cloud-sql-proxy not found. Downloading..."
        curl -o cloud-sql-proxy \
            "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.0/cloud-sql-proxy.linux.amd64"
        chmod +x cloud-sql-proxy
    fi

    # Start proxy in background
    ./cloud-sql-proxy "${CONN_NAME}" --port=5433 &
    PROXY_PID=$!
    sleep 3

    info "Injecting data via proxy (localhost:5433)..."
    PROXY_URL="postgresql://${DB_USER}:${DB_PASSWORD}@localhost:5433/${DB_NAME}"
    DATABASE_URL="${PROXY_URL}" CSV_FILE="${CSV_FILE}" python backend/ingest_data.py

    kill "${PROXY_PID}" 2>/dev/null || true
    success "Data ingestion complete"
else
    warn "CSV file '${CSV_FILE}' not found in current directory."
    warn "To ingest data manually:"
    warn "  1. Start Cloud SQL Auth Proxy: ./cloud-sql-proxy ${CONN_NAME} --port=5433"
    warn "  2. Run: DATABASE_URL=postgresql://${DB_USER}:PASSWORD@localhost:5433/${DB_NAME} python backend/ingest_data.py"
fi

# ── Seed events ────────────────────────────────────────────────────────────────
info "Seeding sample events..."
sleep 5  # Wait for Cloud Run cold start
curl -s -X POST "${SERVICE_URL}/api/v1/seed-events" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Events seeded:', d.get('count', '?'))" 2>/dev/null || \
    warn "Could not auto-seed events. Visit ${SERVICE_URL} and click 'Seed Sample Events'."

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "  ${GREEN}Deployment complete!${NC}"
echo "  URL     : ${SERVICE_URL}"
echo "  DB      : ${CONN_NAME}"
echo "  Image   : ${IMAGE}"
echo ""
echo "  Next steps:"
echo "  1. Open ${SERVICE_URL} in your browser"
echo "  2. If events are missing, click 'Seed Sample Events'"
echo "  3. To run the phase curve calibration (optional):"
echo "     python backend/calibrate_phase_curve.py \\"
echo "       --kaggle Banglore_traffic_Dataset.csv \\"
echo "       --violations '${CSV_FILE}'"
echo "     Then redeploy: ./deploy.sh"
echo "============================================================"
