#!/usr/bin/env bats

load helpers/setup
load helpers/mocks

setup() {
    export AGENT_WORKSPACE="$(mktemp -d)"
    MOCK_DIR="$(mktemp -d)"
    export PATH="${MOCK_DIR}:${PATH}"

    TEST_REPO="$(mktemp -d)"
    git -C "$TEST_REPO" init --quiet
    git -C "$TEST_REPO" commit --allow-empty -m "init" --quiet
    cd "$TEST_REPO"
}

teardown() {
    rm -rf "$AGENT_WORKSPACE" "$MOCK_DIR" "$TEST_REPO"
}

@test "invalid provider exits 1" {
    run "${REPO_ROOT}/bin/agent-ci-watch" invalid
    [ "$status" -eq 1 ]
    [[ "$output" == *"usage:"* ]]
}

@test "github timeout exits 2" {
    export AGENT_CI_TIMEOUT=1
    export POLL_INTERVAL=1
    create_mock gh 0 "[]"
    run "${REPO_ROOT}/bin/agent-ci-watch" github
    [ "$status" -eq 2 ]
    [[ "$output" == *"timeout"* ]]
}

@test "github all success exits 0" {
    export AGENT_CI_TIMEOUT=5
    create_dispatch_mock gh 'echo "[{\"status\":\"completed\",\"conclusion\":\"success\"}]"'
    create_dispatch_mock jq '
        case "$1" in
            "length") echo "1" ;;
            *completed*success*) echo "0" ;;
            *completed*) echo "1" ;;
        esac
    '
    run "${REPO_ROOT}/bin/agent-ci-watch" github
    [ "$status" -eq 0 ]
}
