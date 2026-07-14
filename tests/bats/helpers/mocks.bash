create_mock() {
    local cmd="$1"
    local exit_code="${2:-0}"
    local stdout="${3:-}"
    cat > "${MOCK_DIR}/${cmd}" <<MOCK
#!/usr/bin/env bash
echo "\$@" >> "${MOCK_DIR}/${cmd}.calls"
echo "${stdout}"
exit ${exit_code}
MOCK
    chmod +x "${MOCK_DIR}/${cmd}"
}

create_dispatch_mock() {
    local cmd="$1"
    shift
    local body="$*"
    cat > "${MOCK_DIR}/${cmd}" <<MOCK
#!/usr/bin/env bash
echo "\$@" >> "${MOCK_DIR}/${cmd}.calls"
${body}
MOCK
    chmod +x "${MOCK_DIR}/${cmd}"
}

mock_was_called_with() {
    local cmd="$1"
    local pattern="$2"
    grep -q "$pattern" "${MOCK_DIR}/${cmd}.calls" 2>/dev/null
}
