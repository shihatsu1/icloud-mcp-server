"""release.sh against a fake `gcloud` and `curl`: settings drift or a failed smoke test must stop traffic moving."""

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "cloudrun" / "release.sh"

FAKE_GCLOUD = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo "$*" >> "$FAKE_DIR/args.log"
    case "$*" in
      *"run deploy"*) touch "$FAKE_DIR/deployed"; exit 0;;
      *"update-traffic"*) exit 0;;
      *"run services describe"*) [ -f "$FAKE_DIR/deployed" ] && cat "$FAKE_DIR/service_after.json" || cat "$FAKE_DIR/service_before.json"; exit 0;;
      *"run revisions describe rev-old"*) cat "$FAKE_DIR/rev-old.json"; exit 0;;
      *"run revisions describe rev-new"*) cat "$FAKE_DIR/rev-new.json"; exit 0;;
    esac
    exit 0
    """
)
FAKE_CURL = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    case "$*" in
      *"-X POST"*) printf '%s' "${FAKE_MCP_CODE:-401}";;
      *) [ "${FAKE_PING:-ok}" = "fail" ] && exit 22 || printf 'ok';;
    esac
    """
)


def revision(writes="true", names="TEST,Personal", image="img@sha256:old"):
    env = [
        {"name": "ALLOW_WRITES", "value": writes},
        {"name": "WRITABLE_CALENDARS", "value": names},
        {"name": "ICLOUD_APP_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "icloud-app-password"}}},
    ]
    return {
        "metadata": {"annotations": {"autoscaling.knative.dev/maxScale": "1"}},
        "spec": {"serviceAccountName": "icloud-mcp-run@p.iam.gserviceaccount.com", "containers": [{"image": image, "env": env}]},
    }


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gcloud", FAKE_GCLOUD), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    before = {"status": {"traffic": [{"revisionName": "rev-old", "percent": 100}], "latestCreatedRevisionName": "rev-old"}}
    after = {"status": {"traffic": [{"revisionName": "rev-old", "percent": 100}, {"tag": "candidate", "url": "https://candidate.example"}],
                        "latestCreatedRevisionName": "rev-new"}}
    (fake / "service_before.json").write_text(json.dumps(before))
    (fake / "service_after.json").write_text(json.dumps(after))
    (fake / "rev-old.json").write_text(json.dumps(revision()))
    (fake / "rev-new.json").write_text(json.dumps(revision(image="img@sha256:new")))
    state = tmp_path / "state"
    base = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DIR": str(fake), "RELEASE_STATE_DIR": str(state), "ALLOW_DIRTY": "1"}
    return fake, state, base


def run(base, **extra):
    return subprocess.run(["bash", str(SCRIPT)], env={**base, **extra}, text=True, capture_output=True, cwd=REPO, timeout=60, check=False)


def calls(fake):
    return (fake / "args.log").read_text()


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_a_clean_release_deploys_without_traffic_keeps_settings_and_prints_the_rollback(env):
    fake, state, base = env
    result = run(base)
    assert result.returncode == 0, result.stderr
    log = calls(fake)
    assert "--no-traffic" in log and "--tag candidate" in log
    for forbidden in ("--set-env-vars", "--update-env-vars", "--set-secrets", "--remove-env-vars"):
        assert forbidden not in log  # live settings are not touched
    assert "update-traffic" in log and "rev-new=100" in log
    saved = (state / "last-release.txt").read_text()
    assert "revision=rev-old" in saved and "image=img@sha256:old" in saved
    assert "--to-revisions rev-old=100" in result.stdout  # the exact rollback command
    assert "delete" not in log  # nothing is cleaned up by a release


def test_a_changed_write_setting_aborts_before_traffic_moves(env):
    fake, _state, base = env
    (fake / "rev-new.json").write_text(json.dumps(revision(names="TEST,Personal,Work,School,Church,MLBB")))
    result = run(base)
    assert result.returncode != 0 and "settings differ" in result.stderr
    assert "update-traffic" not in calls(fake)


def test_writes_switched_on_by_a_release_is_caught(env):
    fake, _state, base = env
    (fake / "rev-old.json").write_text(json.dumps(revision(writes="")))
    result = run(base)
    assert result.returncode != 0 and "update-traffic" not in calls(fake)


def test_a_failing_smoke_test_stops_the_release(env):
    fake, _state, base = env
    assert run(base, FAKE_PING="fail").returncode != 0
    assert run(base, FAKE_MCP_CODE="200").returncode != 0
    assert "update-traffic" not in calls(fake)


def test_a_dirty_tree_is_refused_without_the_override(env, tmp_path):
    _fake, _state, base = env
    repo = tmp_path / "dirty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "x").write_text("x")
    base = {k: v for k, v in base.items() if k != "ALLOW_DIRTY"}
    result = subprocess.run(["bash", str(SCRIPT)], env=base, text=True, capture_output=True, cwd=repo, timeout=60, check=False)
    assert result.returncode != 0 and "uncommitted" in result.stderr
