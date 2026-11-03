#!/usr/bin/env bash
# Routine release of the RUNNING service (use deploy.sh only for the first-time bootstrap). Run it yourself:
#   deploy/cloudrun/release.sh
#
# What it does, in order, and what it refuses to do:
#  1. Records the revision and image that serve traffic now (the rollback target) in last-release.txt.
#  2. Deploys the current source as a NEW revision with NO traffic and a "candidate" tag. It passes no
#     env-var or secret flags, so Cloud Run keeps the live settings (write allow-list included).
#  3. Compares the protected settings (write switch, allow-lists, extra redirect URIs, max instances,
#     service account) of the candidate with the serving revision. ANY difference aborts before traffic moves.
#  4. Smoke-tests the candidate URL (/ping is 200, an unauthenticated /mcp is 401).
#  5. Only then moves 100% of traffic, and prints the exact rollback command.
# It never deletes images, revisions or sources (keep the previous ones: they ARE the rollback).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-icloud-mcp-512404}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-icloud-mcp-server}"
STATE_DIR="${RELEASE_STATE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
PROTECTED_ENV="ALLOW_WRITES WRITABLE_CALENDARS WRITABLE_CALENDAR_IDS EXTRA_REDIRECT_URIS"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ABORTED: %s\n' "$*" >&2; exit 1; }
g() { gcloud "$@" --project "$PROJECT_ID" --region "$REGION"; }

if [ -n "$(git status --porcelain 2>/dev/null)" ] && [ "${ALLOW_DIRTY:-0}" != "1" ]; then
  die "the working tree has uncommitted changes, so the release would not be reproducible. Commit them (or set ALLOW_DIRTY=1)."
fi
SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"

# One JSON parser for every field we need; python3 is already required by the project.
field() { python3 -c '
import json, sys
kind, doc = sys.argv[1], json.load(sys.stdin)
if kind == "serving":      # the revision holding 100% of traffic
    print(next(t["revisionName"] for t in doc["status"]["traffic"] if t.get("percent") == 100 and "revisionName" in t))
elif kind == "latest":
    print(doc["status"]["latestCreatedRevisionName"])
elif kind == "candidate_url":
    print(next(t["url"] for t in doc["status"]["traffic"] if t.get("tag") == "candidate"))
elif kind == "image":
    print(doc["spec"]["containers"][0]["image"])
elif kind == "settings":   # a stable fingerprint of everything that must not drift
    c = doc["spec"]["containers"][0]
    env = {e["name"]: e.get("value", "<secret:%s>" % e.get("valueFrom", {}).get("secretKeyRef", {}).get("name", "?")) for e in c.get("env", [])}
    for k in sys.argv[2].split():
        print("%s=%s" % (k, env.get(k, "")))
    meta = doc.get("metadata", {}).get("annotations", {})
    print("maxScale=%s" % meta.get("autoscaling.knative.dev/maxScale", ""))
    print("serviceAccount=%s" % doc["spec"].get("serviceAccountName", ""))
    print("secrets=%s" % ",".join(sorted(e["valueFrom"]["secretKeyRef"]["name"] for e in c.get("env", []) if "valueFrom" in e)))
' "$@"; }

say "1/5 Recording what serves traffic now (the rollback target)"
SERVICE_JSON="$(g run services describe "$SERVICE" --format=json)"
OLD_REV="$(printf '%s' "$SERVICE_JSON" | field serving)"
OLD_IMAGE="$(g run revisions describe "$OLD_REV" --format=json | field image)"
mkdir -p "$STATE_DIR"
{
  echo "revision=$OLD_REV"
  echo "image=$OLD_IMAGE"
  echo "recorded_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "new_source_commit=$SHA"
} > "$STATE_DIR/last-release.txt"
echo "serving: $OLD_REV"

say "2/5 Deploying a candidate revision with NO traffic (live settings are kept)"
g run deploy "$SERVICE" --source . --no-traffic --tag candidate --update-labels "git-sha=$SHA"
SERVICE_JSON="$(g run services describe "$SERVICE" --format=json)"
NEW_REV="$(printf '%s' "$SERVICE_JSON" | field latest)"
[ "$NEW_REV" != "$OLD_REV" ] || die "no new revision was created."
echo "candidate: $NEW_REV"

say "3/5 Checking that protected settings did not change"
OLD_SET="$(g run revisions describe "$OLD_REV" --format=json | field settings "$PROTECTED_ENV")"
NEW_SET="$(g run revisions describe "$NEW_REV" --format=json | field settings "$PROTECTED_ENV")"
if [ "$OLD_SET" != "$NEW_SET" ]; then
  printf 'serving:\n%s\ncandidate:\n%s\n' "$OLD_SET" "$NEW_SET" >&2
  die "settings differ between the serving revision and the candidate. Traffic was NOT moved; $OLD_REV is still live."
fi
echo "unchanged:"; echo "$NEW_SET"

say "4/5 Smoke-testing the candidate"
URL="$(printf '%s' "$SERVICE_JSON" | field candidate_url)"
PING="$(curl -fsS --max-time 60 "$URL/ping")" || die "the candidate did not answer /ping. Traffic was NOT moved."
[ "$PING" = "ok" ] || die "unexpected /ping answer. Traffic was NOT moved."
CODE="$(curl -s -o /dev/null --max-time 60 -w '%{http_code}' -X POST "$URL/mcp")"
[ "$CODE" = "401" ] || die "an unauthenticated /mcp answered $CODE, expected 401. Traffic was NOT moved."
echo "ping ok, unauthenticated /mcp is 401"

say "5/5 Moving traffic"
g run services update-traffic "$SERVICE" --to-revisions "$NEW_REV=100"

cat <<DONE

Released $NEW_REV (source commit $SHA). Previous: $OLD_REV ($OLD_IMAGE).
Next: claude.ai > Customize > Connectors > iCloud Calendar > Refresh tools list (a redeploy can change the tool list).

ROLLBACK (instant, needs no rebuild; the old revision and image are kept):
  gcloud run services update-traffic $SERVICE --project $PROJECT_ID --region $REGION --to-revisions $OLD_REV=100
DONE
