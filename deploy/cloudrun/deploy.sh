#!/usr/bin/env bash
# Deploy the iCloud MCP server to Google Cloud Run. Run from the repository root:
#   deploy/cloudrun/deploy.sh YOUR_PROJECT_ID
#
# Secrets are typed at hidden prompts and piped to Secret Manager over stdin. They are never put on a
# command line (visible in `ps`), never echoed, never written to a file. Safe to re-run.
set -euo pipefail

PROJECT_ID="${1:?usage: deploy/cloudrun/deploy.sh PROJECT_ID}"
REGION="${REGION:-us-central1}"          # a Tier 1 region (larger free allowances, per Google's pricing page)
SERVICE="${SERVICE:-icloud-mcp-server}"
SA_NAME="icloud-mcp-run"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

S_APP_PASSWORD="icloud-app-password"
S_GITHUB_SECRET="icloud-mcp-github-client-secret"
S_JWT_KEY="icloud-mcp-jwt-signing-key"

say() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

put_secret() { # put_secret NAME  (reads the value from stdin)
  local name="$1" value
  IFS= read -r value
  [ -n "$value" ] || { echo "empty value for $name; aborting" >&2; exit 1; }
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT_ID" >/dev/null
  else
    printf '%s' "$value" | gcloud secrets create "$name" --replication-policy=automatic --data-file=- --project "$PROJECT_ID" >/dev/null
  fi
  value=""
}

ask() { # ask VARNAME "prompt"
  local __var="$1" __prompt="$2" __val
  read -r -p "$__prompt: " __val
  [ -n "$__val" ] || { echo "a value is required" >&2; exit 1; }
  printf -v "$__var" '%s' "$__val"
}

ask_secret() { # ask_secret SECRET_NAME "prompt"  (hidden input; goes straight to Secret Manager)
  local name="$1" prompt="$2" value
  read -r -s -p "$prompt (hidden): " value
  echo
  [ -n "$value" ] || { echo "a value is required" >&2; exit 1; }
  printf '%s\n' "$value" | put_secret "$name"
  value=""
}

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
BASE_URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

say "Your service URL will be (Google's deterministic URL form)"
echo "  $BASE_URL"
echo "  MCP endpoint for Claude / ChatGPT:  ${BASE_URL}/mcp"
echo
echo "Create a GitHub OAuth App now (GitHub > Settings > Developer settings > OAuth Apps > New):"
echo "  Homepage URL:                ${BASE_URL}"
echo "  Authorization callback URL:  ${BASE_URL}/auth/callback"
read -r -p "Press Enter when the OAuth App exists (you will paste its Client ID and secret next)... " _

say "Enabling Google Cloud APIs (can take a minute)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com billingbudgets.googleapis.com --project "$PROJECT_ID"

say "Details (the secrets are hidden and go straight to Secret Manager)"
ask GITHUB_CLIENT_ID "GitHub OAuth App Client ID"
ask ALLOWED_GITHUB_USER_ID "Your numeric GitHub user ID"
ask ICLOUD_APPLE_ID "Your Apple Account email (the iCloud address)"
ask_secret "$S_GITHUB_SECRET" "GitHub OAuth App Client secret"
ask_secret "$S_APP_PASSWORD" "iCloud app-specific password (NOT your Apple ID password)"

say "Firestore database (holds encrypted OAuth sign-in state)"
if ! gcloud firestore databases describe --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud firestore databases create --location="$REGION" --type=firestore-native --project "$PROJECT_ID"
fi

say "Signing key for OAuth state (created once; never rotated silently)"
if ! gcloud secrets describe "$S_JWT_KEY" --project "$PROJECT_ID" >/dev/null 2>&1; then
  openssl rand -base64 48 | tr -d '\n' | gcloud secrets create "$S_JWT_KEY" --replication-policy=automatic --data-file=- --project "$PROJECT_ID" >/dev/null
fi

say "Service account with the minimum roles"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="iCloud MCP server (Cloud Run)" --project "$PROJECT_ID"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/datastore.user" --condition=None >/dev/null
for s in "$S_APP_PASSWORD" "$S_GITHUB_SECRET" "$S_JWT_KEY"; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" --project "$PROJECT_ID" >/dev/null
done

say "Deploying to Cloud Run (builds the container with Cloud Build)"
gcloud run deploy "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --source . \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --max-instances 1 --min-instances 0 \
  --memory 512Mi --cpu 1 --cpu-boost --timeout 300 \
  --set-env-vars "^|^PUBLIC_BASE_URL=${BASE_URL}|ICLOUD_APPLE_ID=${ICLOUD_APPLE_ID}|GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID}|ALLOWED_GITHUB_USER_ID=${ALLOWED_GITHUB_USER_ID}|STATE_BACKEND=firestore|GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|DEFAULT_TIMEZONE=Asia/Tokyo|DISPLAY_TIMEZONES=Asia/Tokyo,Asia/Manila" \
  --set-secrets "ICLOUD_APP_PASSWORD=${S_APP_PASSWORD}:latest,GITHUB_CLIENT_SECRET=${S_GITHUB_SECRET}:latest,JWT_SIGNING_KEY=${S_JWT_KEY}:latest"

say "Cost guard: a \$1 budget alert (best effort; check it exists in the console)"
BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' 2>/dev/null || true)"
BILLING_ACCOUNT="${BILLING_ACCOUNT#billingAccounts/}"
if [ -n "$BILLING_ACCOUNT" ]; then
  gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" \
    --display-name="icloud-mcp-server guard" --budget-amount=1USD \
    --filter-projects="projects/${PROJECT_NUMBER}" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=1.0 >/dev/null \
    || warn "could not create the budget alert; create one by hand in Billing > Budgets & alerts"
else
  warn "no billing account found on the project; Cloud Run needs one. Link billing, then re-run."
fi

say "Done. Checks to run now:"
echo "  curl -s -o /dev/null -w '%{http_code}\n' ${BASE_URL}/ping        # expect 200"
echo "  curl -si -X POST ${BASE_URL}/mcp | head -5                          # expect 401 + WWW-Authenticate"
echo "  Then add ${BASE_URL}/mcp as a custom connector in Claude (docs/RUNBOOK-CLOUDRUN.md)."
