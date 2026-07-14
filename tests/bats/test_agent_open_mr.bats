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
    git -C "$TEST_REPO" checkout -b feature-branch --quiet
    cd "$TEST_REPO"
}

teardown() {
    rm -rf "$AGENT_WORKSPACE" "$MOCK_DIR" "$TEST_REPO"
}

@test "no arguments exits 1" {
    run "${REPO_ROOT}/bin/agent-open-mr"
    [ "$status" -eq 1 ]
    [[ "$output" == *"usage:"* ]]
}

@test "on default branch exits 1" {
    cd "$(mktemp -d)"
    git init --quiet
    git commit --allow-empty -m "init" --quiet
    create_dispatch_mock glab 'echo "{\"default_branch\":\"main\"}"'
    create_mock jq 0 "main"
    run "${REPO_ROOT}/bin/agent-open-mr" "test title"
    [ "$status" -eq 1 ]
    [[ "$output" == *"default branch"* ]]
}

@test "attribution footer present in MR body" {
    create_dispatch_mock glab 'if [[ "$1" == "repo" ]]; then echo "{\"default_branch\":\"main\"}"; else echo "https://gitlab.com/org/repo/-/merge_requests/1"; fi'
    create_mock jq 0 "main"
    create_mock git
    run "${REPO_ROOT}/bin/agent-open-mr" "test title" "test body"
    [ "$status" -eq 0 ]
    mock_was_called_with glab "Claude Code"
}
