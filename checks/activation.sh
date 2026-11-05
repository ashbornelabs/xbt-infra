#!/usr/bin/env bash
# Checks that the regtest chain crossed the BLAKE2b activation: the fork and RDTS are active, the last
# block before the activation has an 80-byte header and the activation block a 164-byte one.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
readonly XBT="$ROOT/bin/xbt"
readonly LEGACY_HEADER_SIZE=80
readonly V2_HEADER_SIZE=164

failures=0

fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

pass() {
  echo "ok: $*"
}

# Header size in bytes of the block at the given height.
header_size() {
  local hex
  hex="$("$XBT" cli getblockheader "$("$XBT" cli getblockhash "$1")" false)"
  echo $((${#hex} / 2))
}

check_deployment() {
  local name="$1" filter="$2" expected_height="$3"
  local deployment
  deployment="$("$XBT" cli getdeploymentinfo | jq -c "$filter")"
  if [[ "$(jq -r '.active' <<<"$deployment")" == true && "$(jq -r '.height' <<<"$deployment")" == "$expected_height" ]]; then
    pass "$name is active from height $expected_height"
  else
    fail "$name: expected active from height $expected_height, got $deployment"
  fi
}

check_header_size() {
  local height="$1" expected="$2"
  local size
  size="$(header_size "$height")"
  if [[ "$size" == "$expected" ]]; then
    pass "header at height $height is $size bytes"
  else
    fail "header at height $height: expected $expected bytes, got $size"
  fi
}

main() {
  local activation tip
  activation="$("$XBT" cli getdeploymentinfo | jq -r '.blake2b.height')"
  tip="$("$XBT" cli getblockcount)"
  if [[ ! "$activation" =~ ^[0-9]+$ ]]; then
    echo "FAIL: the node has no BLAKE2b activation height: is it running with -testactivationheight=blake2b@<height>?" >&2
    exit 1
  fi
  if ((tip < activation)); then
    echo "FAIL: the chain is at height $tip, below the activation at $activation: run bin/xbt mine $((activation - tip))" >&2
    exit 1
  fi

  check_deployment "BLAKE2b" '.blake2b' "$activation"
  check_deployment "RDTS" '.deployments.reduced_data' "$activation"
  check_header_size "$((activation - 1))" "$LEGACY_HEADER_SIZE"
  check_header_size "$activation" "$V2_HEADER_SIZE"
  check_header_size "$tip" "$V2_HEADER_SIZE"

  if ((failures > 0)); then
    echo "$failures check(s) failed" >&2
    exit 1
  fi
}

main "$@"
