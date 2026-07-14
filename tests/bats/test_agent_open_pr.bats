#!/usr/bin/env bats

load helpers/setup
load helpers/mocks

setup() {
    export AGENT_WORKSPACE="$(mktemp -d)"
    MOCK_DIR="$(mktemp -d)"
    export PATH="${MOCK_DIR}:${PATH}"

    # Create a temp git repo on a feature branch
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
    run "${REPO_ROOT}/bin/agent-open-pr"
    [ "$status" -eq 1 ]
    [[ "$output" == *"usage:"* ]]
}

@test "on default branch exits 1" {
    cd "$(mktemp -d)"
    git init --quiet
    git commit --allow-empty -m "init" --quiet
    create_dispatch_mock gh 'echo "main"'
    run "${REPO_ROOT}/bin/agent-open-pr" "test title"
    [ "$status" -eq 1 ]
    [[ "$output" == *"default branch"* ]]
}

@test "attribution footer present in PR body" {
    create_dispatch_mock gh 'if [[ "$1" == "repo" ]]; then echo "main"; else echo "https://github.com/org/repo/pull/1"; fi'
    create_mock git
    run "${REPO_ROOT}/bin/agent-open-pr" "test title" "test body"
    [ "$status" -eq 0 ]
    mock_was_called_with gh "Claude Code"
}
