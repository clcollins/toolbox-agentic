#!/bin/bash
# Validate Containerfile base images use pinned tags from trusted registries.

set -euo pipefail

CONTAINERFILE="${1:-Containerfile}"

if [[ ! -f "${CONTAINERFILE}" ]]; then
  echo "ERROR: ${CONTAINERFILE} not found"
  exit 1
fi

TRUSTED_REGISTRIES=(
  "registry.access.redhat.com"
  "registry.redhat.io"
  "registry.fedoraproject.org"
  "quay.io"
  "ghcr.io"
)

errors=0
found_from=false

while IFS= read -r line; do
  found_from=true

  # shellcheck disable=SC2206
  tokens=(${line})
  image=""
  for ((i=1; i<${#tokens[@]}; i++)); do
    if [[ "${tokens[$i]}" != --* ]]; then
      image="${tokens[$i]}"
      break
    fi
  done

  if [[ -z "${image}" ]]; then
    echo "ERROR: could not parse image from: ${line}"
    errors=$((errors + 1))
    continue
  fi

  image_name="${image##*/}"

  if [[ "${image}" == *"@sha256:"* ]]; then
    : # pinned by digest, acceptable
  elif [[ "${image_name}" == *":latest" ]]; then
    echo "ERROR: :latest tag used: ${image}"
    errors=$((errors + 1))
  elif [[ "${image_name}" != *":"* ]]; then
    echo "ERROR: no tag specified (implicit :latest): ${image}"
    errors=$((errors + 1))
  fi

  trusted=false
  for registry in "${TRUSTED_REGISTRIES[@]}"; do
    if [[ "${image}" == "${registry}/"* ]]; then
      trusted=true
      break
    fi
  done

  if [[ "${trusted}" == "false" ]]; then
    echo "ERROR: untrusted registry: ${image}"
    errors=$((errors + 1))
  fi

done < <(grep -iE '^[[:space:]]*FROM[[:space:]]' "${CONTAINERFILE}" || true)

if [[ "${found_from}" == "false" ]]; then
  echo "ERROR: no FROM lines found in ${CONTAINERFILE}"
  exit 1
fi

if [[ ${errors} -gt 0 ]]; then
  echo "FAIL: ${errors} Containerfile validation error(s) found"
  exit 1
fi

echo "OK: ${CONTAINERFILE} validation passed"
