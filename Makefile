-include .env

# ---------------------------------------------------------------------------
# Container engine
# ---------------------------------------------------------------------------
CONTAINER_SUBSYS ?= podman

# ---------------------------------------------------------------------------
# Project metadata
# ---------------------------------------------------------------------------
NAME := toolbox-agentic
PROJECT := clcollins
IMAGE_REGISTRY ?= quay.io

CONTAINER_FILE := Containerfile
PROXY_CONTAINER_FILE := egress-proxy/Containerfile
CI_CONTAINER_FILE := test/Containerfile.ci
CI_IMAGE := $(NAME)-ci
IMAGE_STRING := $(IMAGE_REGISTRY)/$(PROJECT)/$(NAME)

GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_COMMIT := $(shell git rev-parse HEAD 2>/dev/null || echo "unknown")
BUILD_DATE := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
VERSION ?= dev

# ---------------------------------------------------------------------------
# Image names
# ---------------------------------------------------------------------------
IMAGE ?= localhost/agent-runner:go
PROXY_IMAGE ?= localhost/agent-egress-proxy:latest
BASE_IMAGE ?= localhost/agent-runner:go
OUT_IMAGE ?= localhost/agent-runner:go-offline

# ---------------------------------------------------------------------------
# Containerfile build-args
# ---------------------------------------------------------------------------
GO_VERSION ?= 1.26.5
GO_SHA256_AMD64 ?= 5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053
GO_SHA256_ARM64 ?= fe4789e92b1f33358680864bbe8704289e7bb5fc207d80623c308935bd696d49
GLAB_VERSION ?= 1.107.0
RG_VERSION ?= 15.1.0
GO_PREBAKE_TOOLCHAINS ?=

# ---------------------------------------------------------------------------
# Claude auth (one required)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY ?=
CLAUDE_CODE_USE_VERTEX ?=
VERTEXAI_PROJECT ?=
VERTEXAI_LOCATION ?=
ANTHROPIC_VERTEX_PROJECT_ID ?=
CLOUD_ML_REGION ?=
GOOGLE_APPLICATION_CREDENTIALS ?=
GOOGLE_APPLICATION_CREDENTIALS_JSON ?=

# ---------------------------------------------------------------------------
# Git / forge auth
# ---------------------------------------------------------------------------
GH_TOKEN ?=
GITLAB_TOKEN ?=
GITLAB_HOST ?=
GIT_AUTHOR_NAME ?=
GIT_AUTHOR_EMAIL ?=

# ---------------------------------------------------------------------------
# Agent behavior
# ---------------------------------------------------------------------------
AGENT_TASK ?=
AGENT_TASK_FILE ?=
AGENT_REPOS ?=
AGENT_CONTROL_REPO ?=
AGENT_MODE ?= online
AGENT_INTERACTIVE ?=
AGENT_CACHE_ONLY ?=
AGENT_WARM_TOOLCHAINS ?=
AGENT_WARM_MODCACHE ?=
AGENT_GO_WORK ?=
AGENT_GOCACHE_SRC ?=
AGENT_CI_TIMEOUT ?=
AGENT_WORKSPACE ?=
GOPRIVATE ?=

# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------
EGRESS_PROFILE ?=
PROXY_PORT ?=

# ---------------------------------------------------------------------------
# Sources (for validation targets)
# ---------------------------------------------------------------------------
SHELL_SCRIPTS := bin/agent-clone bin/agent-open-pr bin/agent-open-mr bin/agent-ci-watch \
                 run-podman.sh make-offline-cache.sh
PYTHON_SOURCES := bootstrap.py egress-proxy/policy.py
CONTAINERFILES := Containerfile egress-proxy/Containerfile

# ============================================================================
# Targets
# ============================================================================

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Image build targets
# ---------------------------------------------------------------------------

.PHONY: image-build
image-build: ## Build agent-runner image
	$(CONTAINER_SUBSYS) build -f $(CONTAINER_FILE) \
		--build-arg GO_VERSION=$(GO_VERSION) \
		--build-arg GO_SHA256_AMD64=$(GO_SHA256_AMD64) \
		--build-arg GO_SHA256_ARM64=$(GO_SHA256_ARM64) \
		--build-arg GLAB_VERSION=$(GLAB_VERSION) \
		--build-arg RG_VERSION=$(RG_VERSION) \
		$(if $(GO_PREBAKE_TOOLCHAINS),--build-arg GO_PREBAKE_TOOLCHAINS="$(GO_PREBAKE_TOOLCHAINS)",) \
		-t $(IMAGE) .

.PHONY: image-build-proxy
image-build-proxy: ## Build egress-proxy image
	$(CONTAINER_SUBSYS) build -f $(PROXY_CONTAINER_FILE) \
		-t $(PROXY_IMAGE) egress-proxy/

.PHONY: image-build-all
image-build-all: image-build image-build-proxy ## Build both images

.PHONY: image-build-multi
image-build-multi: ## Build multi-arch manifest for agent-runner (amd64+arm64)
	$(CONTAINER_SUBSYS) build --platform linux/amd64,linux/arm64 \
		--manifest $(IMAGE) \
		--build-arg GO_VERSION=$(GO_VERSION) \
		--build-arg GO_SHA256_AMD64=$(GO_SHA256_AMD64) \
		--build-arg GO_SHA256_ARM64=$(GO_SHA256_ARM64) \
		--build-arg GLAB_VERSION=$(GLAB_VERSION) \
		--build-arg RG_VERSION=$(RG_VERSION) \
		$(if $(GO_PREBAKE_TOOLCHAINS),--build-arg GO_PREBAKE_TOOLCHAINS="$(GO_PREBAKE_TOOLCHAINS)",) \
		.

.PHONY: image-build-multi-proxy
image-build-multi-proxy: ## Build multi-arch manifest for egress-proxy
	$(CONTAINER_SUBSYS) build --platform linux/amd64,linux/arm64 \
		--manifest $(PROXY_IMAGE) \
		egress-proxy/

.PHONY: image-build-multi-all
image-build-multi-all: image-build-multi image-build-multi-proxy ## Build multi-arch manifests for both

.PHONY: image-push
image-push: image-build ## Build and push agent-runner image
	$(CONTAINER_SUBSYS) push $(IMAGE)

.PHONY: image-push-proxy
image-push-proxy: image-build-proxy ## Build and push egress-proxy image
	$(CONTAINER_SUBSYS) push $(PROXY_IMAGE)

# ---------------------------------------------------------------------------
# Run targets
# ---------------------------------------------------------------------------

.PHONY: run
run: ## Run agent (headless) via run-podman.sh
	./run-podman.sh

.PHONY: run-interactive
run-interactive: ## Run agent interactively
	AGENT_INTERACTIVE=1 ./run-podman.sh

.PHONY: run-offline
run-offline: ## Run agent in offline-go mode
	AGENT_MODE=offline-go ./run-podman.sh

.PHONY: build-offline-cache
build-offline-cache: ## Build offline Go cache image via make-offline-cache.sh
	./make-offline-cache.sh

# ---------------------------------------------------------------------------
# CI / containerized test targets
# ---------------------------------------------------------------------------

.PHONY: ci-build
ci-build: ## Build CI test container
	$(CONTAINER_SUBSYS) build -f $(CI_CONTAINER_FILE) -t $(CI_IMAGE) .

.PHONY: ci-all
ci-all: ci-build ## Build CI container and run all checks inside it
	$(CONTAINER_SUBSYS) run --rm -v $$(pwd):/src:Z -w /src $(CI_IMAGE) make ci-checks

.PHONY: ci-checks
ci-checks: lint test-python test-shell test-security docs-check ## Run all checks (inside CI container)

# ---------------------------------------------------------------------------
# Linting (runs inside CI container via ci-checks)
# ---------------------------------------------------------------------------

.PHONY: lint
lint: lint-python lint-shell lint-container lint-json lint-yaml validate-containerfile ## Run all linters

.PHONY: lint-python
lint-python: ## Compile-check and lint Python sources
	python3 -m py_compile bootstrap.py
	python3 -m py_compile egress-proxy/policy.py
	ruff check .
	ruff format --check .

.PHONY: lint-shell
lint-shell: ## Lint shell scripts (shellcheck + bash -n)
	shellcheck --shell=bash $(SHELL_SCRIPTS)
	@for f in $(SHELL_SCRIPTS); do \
		bash -n "$$f" || exit 1; \
	done

.PHONY: lint-container
lint-container: ## Lint Containerfiles with hadolint
	hadolint Containerfile
	hadolint egress-proxy/Containerfile

.PHONY: lint-json
lint-json: ## Validate JSON files
	jq . claude/settings.json > /dev/null

.PHONY: lint-yaml
lint-yaml: ## Lint YAML files (k8s manifests)
	yamllint k8s/

.PHONY: validate-containerfile
validate-containerfile: ## Validate Containerfile base image tags and registries
	@for f in $(CONTAINERFILES); do \
		bash test/scripts/check-containerfile-tags.sh "$$f" || exit 1; \
	done

# ---------------------------------------------------------------------------
# Tests (runs inside CI container via ci-checks)
# ---------------------------------------------------------------------------

.PHONY: test-python
test-python: ## Run pytest unit tests (bootstrap.py, policy.py)
	pytest tests/ -m "not integration" -v

.PHONY: test-integration
test-integration: ## Run pytest integration tests (real proxy server)
	pytest tests/ -m integration -v --timeout=30

.PHONY: test-shell
test-shell: ## Run bats tests for bin/agent-* scripts
	bats tests/bats/

.PHONY: test-security
test-security: ## Run security contract and consistency tests
	pytest tests/test_security_contracts.py tests/test_consistency.py -v

.PHONY: docs-check
docs-check: ## Verify plan documents exist in docs/plans/
	@test -d docs/plans || { echo "ERROR: docs/plans/ directory not found"; exit 1; }

# ---------------------------------------------------------------------------
# Aggregate test targets
# ---------------------------------------------------------------------------

.PHONY: test
test: ci-all ## Alias for ci-all (containerized)

.PHONY: test-all
test-all: ci-all image-build-all ## Run all checks + build both images

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove dangling agent volumes and networks
	-$(CONTAINER_SUBSYS) volume ls --format '{{.Name}}' | grep '^agent-' | xargs -r $(CONTAINER_SUBSYS) volume rm -f
	-$(CONTAINER_SUBSYS) network ls --format '{{.Name}}' | grep '^agent-' | xargs -r $(CONTAINER_SUBSYS) network rm -f
