#!/usr/bin/env bats

load helpers/setup
load helpers/mocks

@test "missing auth exits with error" {
    unset ANTHROPIC_API_KEY
    unset CLAUDE_CODE_USE_VERTEX
    export AGENT_REPOS="github.com/org/repo"
    export AGENT_TASK="test"
    create_mock podman
    run bash "${REPO_ROOT}/run-podman.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"ANTHROPIC_API_KEY"* ]] || [[ "$output" == *"CLAUDE_CODE_USE_VERTEX"* ]]
}

@test "vertex auth without ADC file exits with error" {
    export CLAUDE_CODE_USE_VERTEX=1
    export VERTEXAI_PROJECT=test
    export GOOGLE_APPLICATION_CREDENTIALS=/nonexistent/adc.json
    export AGENT_REPOS="github.com/org/repo"
    export AGENT_TASK="test"
    create_mock podman
    run bash "${REPO_ROOT}/run-podman.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"ADC"* ]]
}

@test "missing AGENT_REPOS exits with error" {
    export ANTHROPIC_API_KEY=test
    export AGENT_TASK="test"
    unset AGENT_REPOS
    # Mock podman to return a valid IP for inspect so the script reaches the AGENT_REPOS check
    create_dispatch_mock podman 'if [[ "$1" == "inspect" ]]; then echo "10.0.0.1"; else echo ""; fi'
    run bash "${REPO_ROOT}/run-podman.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"AGENT_REPOS"* ]]
}

@test "missing AGENT_TASK exits with error" {
    export ANTHROPIC_API_KEY=test
    export AGENT_REPOS="github.com/org/repo"
    unset AGENT_TASK
    # Mock podman to return a valid IP for inspect so the script reaches the AGENT_TASK check
    create_dispatch_mock podman 'if [[ "$1" == "inspect" ]]; then echo "10.0.0.1"; else echo ""; fi'
    run bash "${REPO_ROOT}/run-podman.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"AGENT_TASK"* ]]
}
