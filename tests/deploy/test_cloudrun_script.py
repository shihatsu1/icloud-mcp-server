"""Run deploy.sh against a fake `gcloud` that records what it is given.

Proves the script's handling of secrets: they reach Secret Manager over stdin only, and never appear
in any command line (visible in `ps`) or in the script's own output. It does not prove the real
gcloud flags are accepted by Google: that is checked by the first real deployment.
"""

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "cloudrun" / "deploy.sh"

# Synthetic. Built at runtime so no source line looks like a real credential.
GITHUB_SECRET = "ghsecret-" + "SYNTHETIC" + "-0123456789"
APP_PASSWORD = "-".join(["wxyz", "wxyz", "wxyz", "wxyz"])  # noqa: FLY002 - not a literal on purpose
GITHUB_ID = "Iv1.synthetic-client-id"
APPLE_ID = "synthetic.user@example.com"
OWNER_ID = "424242"

FAKE_GCLOUD = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo "$*" >> "$FAKE_LOG_DIR/args.log"
    case " $* " in *" --data-file=- "*) { echo "--- $1 $2 $3"; cat; echo; } >> "$FAKE_LOG_DIR/stdin.log";; esac
    case "$1 $2" in
      "projects describe") echo 123456789;;
      "billing projects") echo "billingAccounts/AAAAAA-BBBBBB-CCCCCC";;
      "secrets describe") exit 1;;  # nothing exists yet, so the create paths run
      "firestore databases"|"iam service-accounts") [ "$3" = "describe" ] && exit 1 || exit 0;;
    esac
    exit 0
    """
)


@pytest.fixture
def run_script(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(FAKE_GCLOUD)
    gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
    logs = tmp_path / "logs"
    logs.mkdir()

    def go():
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG_DIR": str(logs)}
        typed = f"\n{GITHUB_ID}\n{OWNER_ID}\n{APPLE_ID}\n{GITHUB_SECRET}\n{APP_PASSWORD}\n"
        result = subprocess.run(
            ["bash", str(SCRIPT), "my-project"], input=typed, text=True, capture_output=True, env=env, cwd=REPO, timeout=60, check=False
        )
        args = (logs / "args.log").read_text()
        stdin = (logs / "stdin.log").read_text() if (logs / "stdin.log").exists() else ""
        return result, args, stdin

    return go


def test_script_syntax_is_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_secrets_travel_only_over_stdin_and_never_appear_in_commands_or_output(run_script):
    result, args, stdin = run_script()
    assert result.returncode == 0, result.stderr
    for secret in (GITHUB_SECRET, APP_PASSWORD):
        assert secret in stdin, "the secret must reach Secret Manager"
        assert secret not in args, "a secret leaked into a command line"
        assert secret not in result.stdout and secret not in result.stderr, "a secret was echoed"
    assert "--data-file=-" in args and "--data-file=/" not in args


def test_deploy_flags_keep_it_free_private_and_small(run_script):
    _result, args, _stdin = run_script()
    deploy = next(line for line in args.splitlines() if line.startswith("run deploy"))
    for flag in ("--allow-unauthenticated", "--max-instances 1", "--min-instances 0", "--region us-central1", "--source ."):
        assert flag in deploy, flag
    assert "STATE_BACKEND=firestore" in deploy
    assert "PUBLIC_BASE_URL=https://icloud-mcp-server-123456789.us-central1.run.app" in deploy
    for name in ("ICLOUD_APP_PASSWORD=", "GITHUB_CLIENT_SECRET=", "JWT_SIGNING_KEY="):
        assert name in deploy
    assert ":latest" in deploy


def test_least_privilege_roles_and_budget_guard(run_script):
    result, args, _stdin = run_script()
    assert "roles/datastore.user" in args and "roles/secretmanager.secretAccessor" in args
    assert "roles/owner" not in args and "roles/editor" not in args
    assert "billing budgets create" in args and "--budget-amount=1USD" in args
    assert "https://icloud-mcp-server-123456789.us-central1.run.app/auth/callback" in result.stdout


def test_a_generated_signing_key_is_created_once_and_not_printed(run_script):
    result, args, stdin = run_script()
    assert "icloud-mcp-jwt-signing-key" in args
    key_blocks = [b for b in stdin.split("--- ") if b.startswith("secrets create icloud-mcp-jwt-signing-key")]
    assert key_blocks and len(key_blocks[0].splitlines()[1]) >= 32
    assert key_blocks[0].splitlines()[1] not in result.stdout


def test_apis_are_enabled_before_any_secret_is_written(run_script):
    # Real run 1 failed because Secret Manager was written to before its API was enabled.
    _result, args, _stdin = run_script()
    lines = args.splitlines()
    enabled = next(i for i, line in enumerate(lines) if line.startswith("services enable"))
    first_secret = next(i for i, line in enumerate(lines) if line.startswith("secrets create"))
    assert enabled < first_secret


def test_env_var_delimiter_does_not_occur_in_any_value(run_script):
    # Real run 2 failed because the gcloud list delimiter was "@", which an email address contains.
    _result, args, _stdin = run_script()
    deploy = next(line for line in args.splitlines() if line.startswith("run deploy"))
    flag = deploy.split("--set-env-vars ", 1)[1].split(" --set-secrets", 1)[0]
    assert flag.startswith("^") and flag[1:].count("^") >= 1, "a custom delimiter is expected: values contain commas"
    delimiter, body = flag[1:].split("^", 1)
    pairs = body.split(delimiter)
    env = dict(pair.split("=", 1) for pair in pairs)
    assert env["ICLOUD_APPLE_ID"] == APPLE_ID
    assert env["DISPLAY_TIMEZONES"] == "Asia/Tokyo,Asia/Manila"
    assert all("=" in pair for pair in pairs)


def test_dockerfile_turns_off_startup_network_check_and_banner():
    # The PyPI update check runs on every cold start; Claude gives OAuth endpoints only 10 seconds.
    text = (REPO / "Dockerfile").read_text()
    assert "FASTMCP_CHECK_FOR_UPDATES=off" in text
    assert "FASTMCP_SHOW_SERVER_BANNER=false" in text
