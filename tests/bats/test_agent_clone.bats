#!/usr/bin/env bats

load helpers/setup
load helpers/mocks

@test "no arguments exits 1" {
    run "${REPO_ROOT}/bin/agent-clone"
    [ "$status" -eq 1 ]
    [[ "$output" == *"usage:"* ]]
}

@test "basic spec clones with correct URL and dest" {
    create_mock git
    run "${REPO_ROOT}/bin/agent-clone" github.com/org/repo
    [ "$status" -eq 0 ]
    mock_was_called_with git "clone.*https://github.com/org/repo.git"
}

@test "ref parsing calls checkout" {
    create_mock git
    run "${REPO_ROOT}/bin/agent-clone" github.com/org/repo@v1.0
    [ "$status" -eq 0 ]
    mock_was_called_with git "checkout v1.0"
}

@test "ref with slashes parsed correctly" {
    create_mock git
    run "${REPO_ROOT}/bin/agent-clone" github.com/org/repo@fix/issue-42
    [ "$status" -eq 0 ]
    mock_was_called_with git "checkout fix/issue-42"
}

@test "already cloned directory exits 0" {
    mkdir -p "${AGENT_WORKSPACE}/github.com__org__repo"
    run "${REPO_ROOT}/bin/agent-clone" github.com/org/repo
    [ "$status" -eq 0 ]
    [[ "$output" == *"already cloned"* ]]
}
