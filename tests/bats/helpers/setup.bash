REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
MOCK_DIR="$(mktemp -d)"

setup() {
    export AGENT_WORKSPACE="$(mktemp -d)"
    export PATH="${MOCK_DIR}:${PATH}"
}

teardown() {
    rm -rf "$AGENT_WORKSPACE"
    rm -rf "$MOCK_DIR"
}
