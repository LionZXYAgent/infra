# Public Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The user selected implementation in this chat.

**Goal:** Move private secret material out of `infra`, sanitize its history, and demonstrate a fresh isolated deployment before a later public visibility change.

**Architecture:** Use a private `LionZXYOrg/infra-secrets` Git submodule for local editing and an independent Flux GitRepository for deployment. Prepare rewritten history in a standalone clone with a private backup. Exercise the deployed configuration in a new isolated Ubuntu VM with disposable encrypted fixtures and self-signed TLS.

**Tech Stack:** Python 3.10+, PyYAML, Git, GitHub CLI, age, SOPS, Flux, K3s, kubectl, OrbStack, git-filter-repo, Gitleaks.

**Spec:** `docs/superpowers/specs/2026-10-12-public-readiness-design.md`

## Global Constraints

- Keep `infra` private; changing visibility is outside this task.
- Keep `K3S_VERSION=v1.35.9+k3s1`, `FLUX_VERSION=v2.9.6`, and `SOPS_VERSION=v3.13.3` unless an actual compatibility failure requires a documented change.
- Keep secret values, age identities, GitHub tokens, and kubeconfigs out of console output, public documents, and public history.
- Preserve the existing encrypted secret and recovery bundle bytes.
- Do not modify a production cluster during the isolated test.
- Publish rewritten shared refs only after a verified candidate and an explicit expected-head lease.

## Review Focus

- An uninitialized private submodule must allow public checks and reject secret mutations clearly.
- Existing GitHub deploy keys and Kubernetes credentials must be verified and reused safely on repeated bootstrap.
- Secret inputs and errors must not leak plaintext through subprocess output or diagnostics.
- A concurrent remote commit must stop the history cutover rather than be overwritten.
- GitHub pull request refs and forks must be reported separately from rewritten branches and tags.

## Task 1: Private module and public validation boundary

**Files:** Modify `scripts/secretctl.py`, `scripts/check.sh`, `.gitignore`, `README.md`; create `.gitmodules`, `tests/test_private_module.py`; move `.sops.yaml` and `bootstrap/sops-age.age` into the new private repository along with existing `secrets/` contents.

**Interfaces:** Consumes current encrypted manifest and namespace index. Produces `secretctl.require_private_module() -> None`, constants `SECRET_DIR`, `SOPS_CONFIG`, `RECOVERY_FILE`, and `secretctl.check_indexes(*, require_secrets: bool = True) -> list[Path]`.

- [ ] Write filesystem tests using temporary directories and patches to module path constants. The missing-module regression must assert `check_indexes(require_secrets=False) == []` with a valid namespace index, and `require_private_module()` raises `ValueError` containing `submodule`.
- [ ] Run `.venv/bin/python -m unittest discover -s tests -v`. Expected: the new tests fail because the optional-module interface is absent.
- [ ] Create the private repo with `gh repo create LionZXYOrg/infra-secrets --private`, preserve encrypted bytes, set its default branch to `main`, commit and push the private contents. Require GitHub metadata to confirm private visibility before pushing files.
- [ ] Update paths and SOPS filename overrides in `secretctl.py`; explicitly require private-module initialization before secret reads/writes. Split public checks from private checks, validate staged Git links without treating commit objects as blob content, and reject staged public Secret manifests and recovery bundles.
- [ ] Replace the tracked secret directory with a submodule using `git submodule add git@github.com:LionZXYOrg/infra-secrets.git secrets`. Update README commands to commit private changes before updating the module pointer.
- [ ] Run the complete unit suite and `bash scripts/check.sh`. Expected: all pass both with the initialized module and in a temporary public checkout without module initialization.
- [ ] Commit with `git add scripts tests README.md .gitignore .gitmodules secrets bootstrap/sops-age.age .sops.yaml` and `git commit -m 'Move encrypted secrets into a private Git module'`.

## Task 2: Bootstrap independent private Flux source

**Files:** Modify `scripts/bootstrap.py`, `bootstrap/settings.env`, `clusters/home/foundation.yaml`, `clusters/home/kustomization.yaml`; create `clusters/home/secrets-source.yaml`; extend `tests/test_bootstrap.py`.

**Interfaces:** Consumes Task 1 constants and module validator. Produces `bootstrap.ensure_secrets_access(kube: list[str], env: dict[str, str], owner: str, repo: str) -> None`; extends `bootstrap.bootstrap_flux(..., branch: str, personal: bool, private: bool, secrets_owner: str, secrets_repo: str) -> None`.

- [ ] Extend the Flux argument test to pass `private=False`, `secrets_owner='example-owner'`, and `secrets_repo='infra-secrets'`; assert `--private=false` and independent credential provisioning before reconciliation. Add tests that private repo metadata is rejected when public, and an existing deploy credential is not silently replaced.
- [ ] Run the suite. Expected: the new signature and independent-source tests fail before implementation.
- [ ] Read recovery configuration from the module and support an explicitly configured or existing local age identity after validating its recipient against the module rules. Verify private-module checkout and remote destination before host changes.
- [ ] Generate private Git deploy credentials with `flux create secret git infra-secrets --url=ssh://git@github.com/OWNER/REPO --ssh-key-algorithm=ed25519 --export`, capturing the YAML privately. Register only the public key with GitHub using `read_only: true`; apply credential YAML through stdin. On an existing credential, validate key registration and read-only permissions instead of regenerating it.
- [ ] Derive infrastructure visibility from GitHub metadata for the Flux bootstrap flag. Add a source with `spec.ref.branch: main`, `spec.secretRef.name: infra-secrets`, `spec.recurseSubmodules: false`; set `cluster-secrets` path to `./` and source name to `infra-secrets`. Keep existing dependency and deletion behavior.
- [ ] Run the complete unit suite, public/private checks, and Kustomize builds. Expected: all pass. Commit with `git commit -am 'Bootstrap Flux with an independent private secrets source'` after staging the new source and tests.

## Task 3: Fresh isolated deployment test

**Files:** Create `tests/isolated/` manifests and a documented reproducible test procedure; keep private fixture material and kubeconfigs under `/private/tmp/infra-public-readiness.Ho8bKI/`.

**Interfaces:** Consumes production provision script and Flux sources. Produces a redacted deployment report with exact tested Git revisions, cluster versions, readiness conditions, TLS and HTTPS checks, and test-resource cleanup results.

- [ ] Create test-only local TLS manifests: a self-signed `ClusterIssuer` and `Certificate` for `demo.infra.test`, plus a demo IngressRoute using the resulting Traefik certificate. The test Kustomization includes the existing namespaces, Helm controllers, and demo application; it excludes production ACME issuers and certificates.
- [ ] Build the isolated Kustomize tree locally. Expected: all references resolve and no production Cloudflare or ACME resources are present.
- [ ] Create Ubuntu with `orb create --isolated --isolate-network --memory 4G --cpus 2 --disk 20G ubuntu:24.04 infra-public-test`. Provision the exact K3s version through `scripts/provision-server.sh` using the VM's assigned IP and a VM-local kubeconfig. Install pinned Flux and wait for its deployments.
- [ ] Generate a disposable age identity and harmless encrypted Secret fixture. Push temporary test refs into the two private repositories; bootstrap read-only Git source credentials without production secrets and reconcile the isolated path. Record the created deploy key IDs and refs for cleanup.
- [ ] Run `kubectl wait` for GitRepository, Flux Kustomization, HelmRelease, Certificate, Deployment, and Pod readiness. Verify the decrypted fixture by a comparison whose stdout is only success/failure. Check the demo HTTPS body with `curl --cacert` using the generated test certificate.
- [ ] Update the fixture in its private test branch; reconcile and verify the new harmless value. Re-run reconciliation. Expected: healthy, idempotent synchronization and no production resources.
- [ ] Save the redacted report and delete only test-created VM, deploy keys, and remote refs. Commit public test fixtures and documentation.

## Task 4: Sanitized history, review, and cutover

**Files:** Private audit clone and bundle at `/private/tmp/infra-public-readiness.Ho8bKI/`; public final verification report and README cutover guidance.

**Interfaces:** Consumes verified migration commits and the immutable original remote ref inventory. Produces sanitized candidate refs, changed-commit map, redacted history scan results, and final fresh-clone verification.

- [ ] Prepare an independent candidate clone with all advertised branches, tags, and pull request heads. Keep the original private bundle unchanged. Use `git-filter-repo --sensitive-data-removal --invert-paths --path secrets/ --path .sops.yaml --path bootstrap/sops-age.age` on original history, then apply the completed migration tree including its new Git link.
- [ ] Enumerate every reachable tree and assert removed private paths are absent as blobs; permit only the final `160000` Git link at `secrets`. Run Gitleaks with `--redact=100 --log-opts=--all`. Expected: no credential findings.
- [ ] Run the whole test suite and both public/private manifest checks against the candidate. Request one independent final code review and resolve important findings before cutover.
- [ ] Verify the private destination contents, capture fresh remote ref heads, and publish sanitized branch/tag refs with exact `--force-with-lease=REF:OLD_SHA`. Expected: a concurrent update rejects the push. Never use `--mirror` to upload private backups or audit refs.
- [ ] Fresh-clone the rewritten remote and repeat path audit, redacted Gitleaks scan, and public checks. Inspect retained `refs/pull/*` and the private fork; report them precisely and keep visibility private until the outstanding publication boundary is resolved.
- [ ] Write final evidence, preserve private recovery artifacts, and provide the new repo link, tested outcome, and any concrete remaining public-readiness blocker.
