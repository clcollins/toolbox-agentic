# Testing

## Automated test suite

Run all tests (creates a `.venv` automatically on first run):

```bash
make test
```

Individual targets:

| Target | What it runs |
|--------|-------------|
| `make lint` | Python (ruff, py_compile), shell (shellcheck, bash -n), Containerfile (hadolint), K8s (kubeconform) |
| `make test-python` | pytest unit tests (entrypoint.py, policy.py) |
| `make test-integration` | pytest integration tests (real proxy server, SSRF, CONNECT) |
| `make test-shell` | bats tests for bin/agent-* scripts and scripts/run-podman.sh |
| `make test-security` | security contract + cross-file consistency tests |
| `make build` | podman build both images |

CI runs all of the above via `.github/workflows/ci.yml`.

## Manual verification

These tests require a running container or external services and are not automated.

### Full live run

```bash
# 1. Build both images
podman build --tag agent-runner:go .
podman build --tag agent-egress-proxy:latest egress-proxy/

# 2. Run with direct API key
ANTHROPIC_API_KEY="sk-ant-..." \
GH_TOKEN="ghp_..." \
AGENT_REPOS="github.com/owner/repo" \
AGENT_TASK="Summarize the repo and propose next steps." \
  ./scripts/run-podman.sh

# 3. Or with Vertex AI
CLAUDE_CODE_USE_VERTEX=1 \
VERTEXAI_PROJECT="your-gcp-project" \
VERTEXAI_LOCATION="global" \
GH_TOKEN="ghp_..." \
AGENT_REPOS="github.com/owner/repo" \
AGENT_TASK="Summarize the repo and propose next steps." \
  ./scripts/run-podman.sh
```

### Manual proxy hardening (HTTPie)

Build and run the proxy, then test each scenario:

```bash
podman build --tag agent-egress-proxy:latest egress-proxy/
podman run -d --name test-proxy -p 18080:8080 agent-egress-proxy:latest

# Malformed Content-Length → 400
http --proxy=http:http://localhost:18080 GET http://example.com/ "Content-Length:abc"

# SSRF to private IP → 403
http --proxy=http:http://localhost:18080 GET http://10.0.0.1/

# CONNECT to untrusted host → 403
curl -x http://localhost:18080 https://evil.example.com/ 2>&1

# Trusted host → success
http --proxy=http:http://localhost:18080 GET http://api.github.com/

podman rm -f test-proxy
```
