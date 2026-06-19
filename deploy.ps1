# =============================================================================
# Gridlock Intelligence -- GCP Deployment Script (PowerShell)
#
# Usage:
#   .\deploy.ps1
#
# Overrides (set before running):
#   $env:PROJECT_ID  = "my-project"
#   $env:REGION      = "asia-south1"
#   $env:DB_PASSWORD = "MyPassword123"
# =============================================================================

$ErrorActionPreference = "Continue"

# ── Configuration ──────────────────────────────────────────────────────────────
$PROJECT_ID   = if ($env:PROJECT_ID)   { $env:PROJECT_ID }   else { (gcloud config get-value project 2>$null).Trim() }
$REGION       = if ($env:REGION)       { $env:REGION }       else { "us-central1" }
$SERVICE_NAME = if ($env:SERVICE_NAME) { $env:SERVICE_NAME } else { "gridlock-intelligence" }
$REPO_NAME    = if ($env:REPO_NAME)    { $env:REPO_NAME }    else { "gridlock" }
$DB_INSTANCE  = if ($env:DB_INSTANCE)  { $env:DB_INSTANCE }  else { "gridlock-db" }
$DB_NAME      = if ($env:DB_NAME)      { $env:DB_NAME }      else { "congestion_db" }
$DB_USER      = if ($env:DB_USER)      { $env:DB_USER }      else { "gridlock_user" }
$SECRET_NAME  = "gridlock-db-url"
$CSV_FILE     = if ($env:CSV_FILE)     { $env:CSV_FILE }     else { "jan to may police violation_anonymized791b166.csv" }

if ($env:DB_PASSWORD) {
    $DB_PASSWORD = $env:DB_PASSWORD
} else {
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $DB_PASSWORD = -join ((1..24) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
}

$IMAGE       = "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"
$BUCKET_NAME = "${PROJECT_ID}-gridlock-data"

# ── Helpers ────────────────────────────────────────────────────────────────────
function Info  { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function OK    { param($m) Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn  { param($m) Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Fail  { param($m) Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

function Assert-Exit {
    param($msg)
    if ($LASTEXITCODE -ne 0) { Fail $msg }
}

# Run a gcloud command that may return non-zero without treating it as a fatal
# error.  Returns $true if the command exited 0, $false otherwise.
# The Windows gcloud.ps1 wrapper emits stderr as PS ErrorRecords; we suppress
# them here so "not found" probes don't crash the script.
function Test-GCloud {
    param([string[]]$GArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    gcloud @GArgs 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

# ── Banner ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor White
Write-Host "  Gridlock Intelligence -- GCP Deployment"                   -ForegroundColor White
Write-Host "  Project : $PROJECT_ID"
Write-Host "  Region  : $REGION"
Write-Host "  Service : $SERVICE_NAME"
Write-Host "============================================================"
Write-Host ""

# ── Preflight checks ───────────────────────────────────────────────────────────
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Fail "gcloud not found. Install from https://cloud.google.com/sdk"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker not found. Install from https://docs.docker.com/get-docker/"
}
if ([string]::IsNullOrEmpty($PROJECT_ID)) {
    Fail "PROJECT_ID not set. Run: gcloud config set project YOUR_PROJECT_ID"
}

gcloud config set project $PROJECT_ID --quiet
Assert-Exit "Failed to set gcloud project"

# ── Enable APIs ────────────────────────────────────────────────────────────────
Info "Enabling GCP APIs..."
gcloud services enable `
    run.googleapis.com `
    sqladmin.googleapis.com `
    artifactregistry.googleapis.com `
    cloudbuild.googleapis.com `
    secretmanager.googleapis.com `
    storage.googleapis.com `
    --quiet
Assert-Exit "Failed to enable APIs"
OK "APIs enabled"

# ── Artifact Registry ──────────────────────────────────────────────────────────
Info "Checking Artifact Registry repository '$REPO_NAME'..."
if (-not (Test-GCloud artifacts, repositories, describe, $REPO_NAME, "--location=$REGION", --quiet)) {
    gcloud artifacts repositories create $REPO_NAME `
        --repository-format=docker `
        --location=$REGION `
        --description="Gridlock Intelligence Docker images" `
        --quiet
    Assert-Exit "Failed to create Artifact Registry repository"
    OK "Repository '$REPO_NAME' created"
} else {
    OK "Repository '$REPO_NAME' already exists"
}

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
Assert-Exit "Failed to configure Docker auth"

# ── Cloud SQL ──────────────────────────────────────────────────────────────────
Info "Checking Cloud SQL instance '$DB_INSTANCE'..."
if (-not (Test-GCloud sql, instances, describe, $DB_INSTANCE, --quiet)) {
    Info "Creating Cloud SQL instance (takes ~5 minutes)..."
    gcloud sql instances create $DB_INSTANCE `
        --database-version=POSTGRES_14 `
        --tier=db-f1-micro `
        --region=$REGION `
        --storage-type=SSD `
        --storage-size=20GB `
        --storage-auto-increase `
        --assign-ip `
        --quiet
    Assert-Exit "Failed to create Cloud SQL instance"
    OK "Cloud SQL instance '$DB_INSTANCE' created"
} else {
    OK "Cloud SQL instance '$DB_INSTANCE' already exists"
}

Info "Creating database '$DB_NAME'..."
if (Test-GCloud sql, databases, create, $DB_NAME, "--instance=$DB_INSTANCE", --quiet) {
    OK "Database '$DB_NAME' created"
} else { Warn "Database '$DB_NAME' already exists" }

Info "Creating database user '$DB_USER'..."
if (Test-GCloud sql, users, create, $DB_USER, "--instance=$DB_INSTANCE", "--password=$DB_PASSWORD", --quiet) {
    OK "User '$DB_USER' created"
} else { Warn "User '$DB_USER' already exists" }

# PostGIS setup and data ingestion are done together later via Cloud SQL Auth Proxy.
# (See the "PostGIS + ingestion via proxy" section below.)

$CONN_NAME = (gcloud sql instances describe $DB_INSTANCE --format="value(connectionName)" 2>$null).Trim()
OK "Cloud SQL connection: $CONN_NAME"

$DB_URL = "postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CONN_NAME}"

# ── Secret Manager ─────────────────────────────────────────────────────────────
# Write via a temp file with no-BOM UTF-8. [System.Text.Encoding]::UTF8 writes a BOM
# which SQLAlchemy cannot parse; [System.Text.UTF8Encoding]::new($false) omits it.
Info "Storing DATABASE_URL in Secret Manager..."
$secretTmp = "$env:TEMP\gridlock_dburl.txt"
[System.IO.File]::WriteAllText($secretTmp, $DB_URL, [System.Text.UTF8Encoding]::new($false))
if (Test-GCloud secrets, describe, $SECRET_NAME, --quiet) {
    gcloud secrets versions add $SECRET_NAME --data-file=$secretTmp --quiet
    OK "Secret '$SECRET_NAME' updated"
} else {
    gcloud secrets create $SECRET_NAME --data-file=$secretTmp --replication-policy=automatic --quiet
    OK "Secret '$SECRET_NAME' created"
}
Remove-Item $secretTmp -ErrorAction SilentlyContinue

# Grant the default Compute SA access to the secret
$PROJECT_NUMBER = (gcloud projects describe $PROJECT_ID --format="value(projectNumber)" 2>$null).Trim()
$COMPUTE_SA = "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding $SECRET_NAME `
    --member="serviceAccount:$COMPUTE_SA" `
    --role="roles/secretmanager.secretAccessor" `
    --quiet 2>$null | Out-Null

# ── Docker build ───────────────────────────────────────────────────────────────
Info "Building Docker image (multi-stage: Node + Python, ~5 min)..."
docker build `
    --tag $IMAGE `
    --build-arg VITE_API_BASE_URL="" `
    --file Dockerfile `
    .
Assert-Exit "Docker build failed"
OK "Image built: $IMAGE"

Info "Pushing image to Artifact Registry..."
docker push $IMAGE
Assert-Exit "Docker push failed"
OK "Image pushed"

# ── Cloud Run deployment ───────────────────────────────────────────────────────
Info "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME `
    --image=$IMAGE `
    --region=$REGION `
    --platform=managed `
    --allow-unauthenticated `
    --memory=2Gi `
    --cpu=2 `
    --min-instances=0 `
    --max-instances=3 `
    --concurrency=80 `
    --timeout=300 `
    --add-cloudsql-instances=$CONN_NAME `
    "--set-secrets=DATABASE_URL=${SECRET_NAME}:latest" `
    "--set-env-vars=ALLOWED_ORIGINS=*" `
    --quiet
Assert-Exit "Cloud Run deployment failed"

$SERVICE_URL = (gcloud run services describe $SERVICE_NAME --region=$REGION --format="value(status.url)" 2>$null).Trim()
OK "Cloud Run deployed: $SERVICE_URL"

# ── PostGIS + data ingestion via Cloud SQL Auth Proxy ──────────────────────────
# ADC (gcloud auth application-default login) is not required.
# The proxy's --gcloud-auth flag uses regular gcloud credentials instead.
Write-Host ""
Info "Starting Cloud SQL Auth Proxy (--gcloud-auth, port 5433)..."

$proxyProc = $null
try {
    # cloud-sql-proxy is bundled with Cloud SDK 573.0.0+ and should be in PATH
    $proxyProc = Start-Process "cloud-sql-proxy" `
        -ArgumentList "--gcloud-auth", $CONN_NAME, "--port=5433" `
        -PassThru -WindowStyle Hidden -ErrorAction Stop
    Start-Sleep -Seconds 5
    OK "Proxy started (PID $($proxyProc.Id))"
} catch {
    Warn "Could not auto-start cloud-sql-proxy: $_"
    Warn "If you already started it manually with --gcloud-auth on port 5433, that is fine."
}

# Set a one-time postgres admin password so we can create the PostGIS extension
# as superuser (gridlock_user lacks the required privilege).
$chars2 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
$POSTGRES_ADMIN_PASS = -join ((1..20) | ForEach-Object { $chars2[(Get-Random -Maximum $chars2.Length)] })
gcloud sql users set-password postgres `
    --instance=$DB_INSTANCE `
    --password=$POSTGRES_ADMIN_PASS `
    --quiet
Assert-Exit "Failed to set postgres admin password"
OK "Postgres admin password set"

Info "Creating PostGIS extension and granting superuser to '$DB_USER'..."
$setupScript = @"
import psycopg2, sys
try:
    conn = psycopg2.connect('postgresql://postgres:${POSTGRES_ADMIN_PASS}@localhost:5433/${DB_NAME}')
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('CREATE EXTENSION IF NOT EXISTS postgis;')
    cur.execute('GRANT cloudsqlsuperuser TO ${DB_USER};')
    conn.close()
    print('PostGIS OK')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
"@
$setupPyPath = "$env:TEMP\gridlock_postgis.py"
$setupScript | Set-Content $setupPyPath -Encoding utf8
python $setupPyPath
Assert-Exit "PostGIS setup failed"
Remove-Item $setupPyPath -ErrorAction SilentlyContinue
OK "PostGIS extension created and superuser granted"

if (Test-Path $CSV_FILE) {
    Info "Creating Cloud Storage bucket for CSV archive..."
    gcloud storage buckets create "gs://$BUCKET_NAME" --location=$REGION --quiet 2>$null | Out-Null
    gcloud storage cp $CSV_FILE "gs://$BUCKET_NAME/violations.csv" --quiet
    OK "CSV archived to gs://$BUCKET_NAME/violations.csv"

    Info "Running data ingestion via proxy (298k records, ~5 min)..."
    $env:DATABASE_URL = "postgresql://${DB_USER}:${DB_PASSWORD}@localhost:5433/${DB_NAME}"
    python backend\ingest_data.py --csv-file $CSV_FILE
    Assert-Exit "Data ingestion failed"
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    OK "Data ingestion complete"
} else {
    Warn "CSV '$CSV_FILE' not found -- skipping ingestion."
    Warn "To ingest manually:"
    Warn "  1. Start proxy: cloud-sql-proxy --gcloud-auth $CONN_NAME --port=5433"
    Warn "  2. `$env:DATABASE_URL = 'postgresql://${DB_USER}:${DB_PASSWORD}@localhost:5433/${DB_NAME}'"
    Warn "  3. python backend\ingest_data.py --csv-file PATH_TO_CSV"
}

# Stop the proxy if we started it
if ($null -ne $proxyProc -and -not $proxyProc.HasExited) {
    $proxyProc.Kill()
    OK "Cloud SQL Auth Proxy stopped"
}

# ── Seed events ────────────────────────────────────────────────────────────────
Info "Seeding sample events..."
Start-Sleep -Seconds 6
try {
    $resp = Invoke-RestMethod -Uri "$SERVICE_URL/api/v1/seed-events" -Method POST -TimeoutSec 30
    OK "Events seeded: $($resp.count) events"
} catch {
    Warn "Could not auto-seed. Open $SERVICE_URL and click 'Seed Sample Events'."
}

# ── Done ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host "  URL  : $SERVICE_URL"
Write-Host "  DB   : $CONN_NAME"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "  1. Open $SERVICE_URL in your browser"
Write-Host "  2. If events are missing, click 'Seed Sample Events'"
Write-Host "  3. Optional - calibrate phase curve after Kaggle download:"
Write-Host "       python backend\calibrate_phase_curve.py --kaggle Banglore_traffic_Dataset.csv"
Write-Host "     Then redeploy: .\deploy.ps1"
Write-Host "============================================================" -ForegroundColor Green
