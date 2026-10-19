#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_dir="$(mktemp -d)"
mkdir "$test_dir/bin"
ruby -ryaml -e 'w = YAML.load_file(ARGV[0]); File.write(ARGV[1], w.fetch("jobs").fetch("sync").fetch("steps").last.fetch("run"))' \
  "$repo_root/.github/workflows/sync.yml" "$test_dir/worker.sh"
bash -n "$test_dir/worker.sh"

cat > "$test_dir/bin/gh" <<'MOCK'
#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$CASE_DIR/calls"
if [[ "$1" == repo ]]; then exit 0; fi
if [[ "$2" == graphql ]]; then
  if [[ "$*" == *qualifiedName* ]]; then
    case "$SCENARIO" in
      missing_branch) sha=null ;;
      custom_branch_current) sha='"upstream"' ;;
      *) sha='"behind"' ;;
    esac
    if [[ "$*" == *'{sha:'* ]]; then
      printf '{"sha":%s}\n' "$sha"
    elif [[ "$sha" != null ]]; then
      printf '%s\n' "$sha"
    fi
  elif [[ "$SCENARIO" == up_to_date ]]; then
    echo '{"branch":"main","upstreamSha":"upstream","forkBranch":"main","forkSha":"upstream"}'
  else
    echo '{"branch":"main","upstreamSha":"upstream","forkBranch":"custom","forkSha":"behind"}'
  fi
  exit 0
fi
if [[ "$*" == *'--method POST'* ]]; then exit 0; fi
if [[ "$*" == *'--method PUT'* ]]; then touch "$CASE_DIR/disabled"; exit 0; fi
case "$SCENARIO" in
  permanent) echo 'gh: HTTP 403: Resource not accessible by integration' >&2; exit 1 ;;
  transient|rate_limited)
    if [[ ! -f "$CASE_DIR/retried" ]]; then
      touch "$CASE_DIR/retried"
      if [[ "$SCENARIO" == rate_limited ]]; then
        echo 'gh: HTTP 429: Too Many Requests' >&2
      else
        echo 'gh: HTTP 502: Bad Gateway' >&2
      fi
      exit 1
    fi ;;
  delayed)
    if [[ ! -f "$CASE_DIR/disabled" ]]; then echo true; exit 0; fi
    if [[ ! -f "$CASE_DIR/propagated" ]]; then
      touch "$CASE_DIR/propagated"
      echo true
      exit 0
    fi ;;
esac
echo false
MOCK
cat > "$test_dir/bin/sleep" <<'MOCK'
#!/bin/bash
printf '%s\n' "$1" >> "$CASE_DIR/sleeps"
MOCK
chmod +x "$test_dir/bin/gh" "$test_dir/bin/sleep"

for scenario in missing_branch up_to_date custom_branch_current transient rate_limited permanent delayed; do
  export SCENARIO="$scenario" CASE_DIR="$test_dir/$scenario"
  mkdir "$CASE_DIR"
  result=0
  PATH="$test_dir/bin:$PATH" OWNER=example REPOSITORIES=opensource-owner__repo \
    bash "$test_dir/worker.sh" > "$CASE_DIR/output" 2>&1 || result=$?
  if [[ "$scenario" == permanent ]]; then
    [[ "$result" != 0 ]]
    [[ "$(wc -l < "$CASE_DIR/calls" | tr -d ' ')" == 1 ]]
  else
    [[ "$result" == 0 ]] || { cat "$CASE_DIR/output"; exit 1; }
    case "$scenario" in
      missing_branch)
        grep -q -- '--method POST repos/example/opensource-owner__repo/git/refs -f ref=refs/heads/main -f sha=upstream' "$CASE_DIR/calls"
        ! grep -q '^repo sync' "$CASE_DIR/calls" ;;
      up_to_date|custom_branch_current)
        ! grep -q '^repo sync\|--method POST\|--method PUT' "$CASE_DIR/calls" ;;
      *) grep -q '^repo sync example/opensource-owner__repo --branch main --force$' "$CASE_DIR/calls" ;;
    esac
  fi
  if [[ "$scenario" == rate_limited ]]; then grep -q '^60$' "$CASE_DIR/sleeps"; fi
  if [[ "$scenario" == delayed ]]; then grep -q -- '--method PUT' "$CASE_DIR/calls"; fi
  printf '%s: PASS\n' "$scenario"
done
