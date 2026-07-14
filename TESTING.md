# Testing

## Static validation (no container needed)

```bash
python3 -m py_compile bootstrap.py
python3 -m py_compile egress-proxy/policy.py
bash -n run-podman.sh make-offline-cache.sh bin/agent-*
python3 -c "import json; json.load(open('claude/settings.json'))"
python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['k8s/job.yaml','k8s/secret.example.yaml','k8s/networkpolicy.yaml']]"
```

## Bootstrap logic (no container needed)

Run these from the repo root. Each should print a `[bootstrap][FATAL]` message and exit 1.

```bash
# Whitespace-only AGENT_TASK should fail
AGENT_REPOS="github.com/foo/bar" AGENT_TASK="   " python3 -c "
import bootstrap
bootstrap.materialize_adc()
bootstrap.preflight()
"

# Missing AGENT_TASK_FILE should fail
AGENT_REPOS="github.com/foo/bar" AGENT_TASK_FILE="/nonexistent" python3 -c "
import bootstrap
bootstrap.materialize_adc()
bootstrap.preflight()
"

# Conflicting refs should fail
AGENT_REPOS="github.com/foo/bar@main github.com/foo/bar@v1.0" \
AGENT_TASK="test" ANTHROPIC_API_KEY="fake" python3 -c "
import bootstrap
bootstrap.materialize_adc()
bootstrap.preflight()
list(bootstrap.repo_specs())
"
```

## Proxy hardening

Build and run the proxy locally:

```bash
podman build --tag agent-egress-proxy:latest egress-proxy/
podman run -d --name test-proxy -p 18080:8080 agent-egress-proxy:latest
```

Each of these should be denied (400 or 403):

```bash
# Malformed Content-Length → 400
http --proxy=http:http://localhost:18080 GET http://example.com/ "Content-Length:abc"

# SSRF to private IP → 403
http --proxy=http:http://localhost:18080 GET http://10.0.0.1/

# SSRF to localhost → 403
http --proxy=http:http://localhost:18080 GET http://127.0.0.1:8080/

# SSRF to cloud metadata endpoint → 403
http --proxy=http:http://localhost:18080 GET http://169.254.169.254/latest/

# CONNECT to untrusted host → 403
curl -x http://localhost:18080 https://evil.example.com/ 2>&1
```

This should succeed (trusted host):

```bash
http --proxy=http:http://localhost:18080 GET http://api.github.com/
```

Cleanup:

```bash
podman rm -f test-proxy
```

## Dest path naming

Verify that `clone_one()` now includes the host in the workspace directory name:

```bash
AGENT_WORKSPACE=/tmp/agent-test-ws \
AGENT_REPOS="github.com/your-org/your-repo" \
AGENT_TASK="test" ANTHROPIC_API_KEY="fake" python3 -c "
import bootstrap, os
os.makedirs('/tmp/agent-test-ws', exist_ok=True)
specs = list(bootstrap.repo_specs())
for s in specs:
    spec = s; ref = None
    if '@' in spec: spec, ref = spec.split('@', 1)
    host, _, path = spec.partition('/')
    print(f'{s} -> {host}__{path.replace(\"/\", \"__\")}')
"
```

Should print `github.com/your-org/your-repo -> github.com__your-org__your-repo`.

## Full live run

See the "Usage" section in the main README, or the quick-start below:

```bash
# 1. Build both images
podman build --tag agent-runner:go .
podman build --tag agent-egress-proxy:latest egress-proxy/

# 2. Run with direct API key
ANTHROPIC_API_KEY="sk-ant-..." \
GH_TOKEN="ghp_..." \
AGENT_REPOS="github.com/owner/repo" \
AGENT_TASK="Summarize the repo and propose next steps." \
  ./run-podman.sh

# 3. Or with Vertex AI
CLAUDE_CODE_USE_VERTEX=1 \
VERTEXAI_PROJECT="your-gcp-project" \
VERTEXAI_LOCATION="global" \
GH_TOKEN="ghp_..." \
AGENT_REPOS="github.com/owner/repo" \
AGENT_TASK="Summarize the repo and propose next steps." \
  ./run-podman.sh
```
