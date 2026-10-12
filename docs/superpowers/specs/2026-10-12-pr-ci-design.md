# GitHub Actions PR Checks Design

## Intent and acceptance criteria

Every pull request must be testable without access to `infra-secrets` or production
credentials. GitHub Actions will validate the public checkout and deploy its real
infrastructure and demo application from scratch on an ephemeral Ubuntu runner.
The deployment uses generated credentials, encrypted harmless fixtures, and local
TLS. Code introduced by this work must have regression tests. Future changes must
include appropriate tests as a documented review requirement; CI will not impose
a test-file-change gate.

Success means both workflow jobs pass for this implementation PR, including fresh
server provisioning, source authentication, SOPS decryption, controller readiness,
certificate-verified HTTPS, secret updates, and repeat reconciliation. Production
DNS/ACME and the workstation's SSH-to-server wrapper remain outside this test.

## Workflow

Create `.github/workflows/pr-checks.yaml` with `pull_request`, pushes to `master`,
and manual dispatch triggers. Use stable job names `Validate` and `Deployment`.
The deployment job depends on validation. Run both jobs for all PRs, including
documentation changes, so required check names always appear. Use Ubuntu 24.04
GitHub-hosted runners, bounded job timeouts, and concurrency that cancels obsolete
runs of the same PR or branch.

Use `contents: read`, SHA-pinned official Actions, and checkout with
`persist-credentials: false`, `submodules: false`, and complete public history.
Use the tested pull-request merge checkout rather than only the contributor's
head. Do not use `pull_request_target`, repository secrets, private submodules,
self-hosted runners, cloud credentials, or production kubeconfigs.

Validation installs the pinned tools and Python requirements, runs all unit tests,
`scripts/check.sh`, Kustomize builds including isolated TLS and demo overlays, and
a redacted Gitleaks history scan. Gitleaks is installed as a checksum-verified
pinned CLI rather than an organization-licensed Action. Keep existing K3s, Flux,
and SOPS version pins unchanged.

## Fresh deployment runner

Provide `tests/ci/run.sh` as the Linux entry point. It refuses a pre-existing K3s
installation or configuration and requires explicit acknowledgement that the
machine is disposable. It determines the host's assigned IPv4 address, invokes
the existing `scripts/provision-server.sh`, and uses a new private kubeconfig for
every Kubernetes command. GitHub Actions disables swap only on its ephemeral
runner before invoking the entry point; the production provisioner's no-swap
guard remains unchanged. A fresh full Ubuntu VM can run the same entry point.

The runner installs the pinned Flux controllers and uses the existing
`tests/isolated` Kustomizations, production namespace manifests, real Traefik and
cert-manager Helm releases, and production demo resources. It overlays only the
two Git source URLs/branches and authentication references. It retains the test
hostname `demo.infra.test` and self-signed issuer, and excludes production ACME,
Cloudflare configuration, recovery bundles, and production Secret manifests.

## Mock Git and secret fixtures

`tests/ci/git_server.py` serves two local Git repositories using Git's smart HTTP
backend. Each repository requires a separately generated basic-auth credential.
The server permits clone/fetch only, rejects push and path traversal, and avoids
request or credential logging. It binds to the ephemeral node so Flux pods can
reach it. HTTP carries only disposable test credentials on this isolated machine.

`tests/ci/fixtures.py` constructs an infrastructure repository from public tracked
files without reading or initializing the secrets Gitlink. It updates the test
source manifest to point at the local secrets repository. The separate secrets
repository contains a root Kustomization and one SOPS-encrypted
`demo/isolated-probe` Secret with a harmless `probe` value. Create a fresh age
identity and explicit SOPS rules for that repository. Remove inherited SOPS key
and Git credential environment settings from fixture subprocesses. Never copy the
private module or use ambient production key discovery.

Apply two Git authentication Secrets and the disposable `sops-age` Secret through
captured stdin. Use explicit local URLs, branch names, and kubeconfig paths.
Sources set `recurseSubmodules: false`. No GitHub deploy keys or temporary remote
branches are created.

## Deployment assertions

`tests/ci/deployment.py` orchestrates these checks with explicit failures:

1. All four Flux Deployments become available.
2. Both GitRepositories, all six Flux Kustomizations, both HelmReleases, the
   isolated certificate, and the demo Deployment become ready.
3. The harmless probe decrypts to its expected initial value.
4. HTTPS at `demo.infra.test` verifies against the issued certificate and returns
   the demo page marker. Never use `--insecure`.
5. A new encrypted probe value is committed to the local secrets source; Flux
   reconciles it and the cluster receives the changed value.
6. A second root reconciliation succeeds, all resources remain ready, and HTTPS
   continues to pass.
7. Production ACME issuers, production certificates, and the Cloudflare Secret
   are absent from the isolated deployment.

Authentication tests independently establish that unauthenticated access,
cross-repository credentials, and pushes are rejected. Fixture regression tests
verify that public exports exclude private material and that generated manifests
retain the intended independent-source and decryption contracts. Runner tests
exercise failed readiness, wrong probe values, HTTPS failure, explicit kubeconfig
selection, and refusal to target an existing installation.

## Failure handling and cleanup

Use a mode-0700 temporary directory and mode-0600 credential files. Remove mock
repositories, identities, credentials, kubeconfig copies, and the HTTP server on
success, failure, and interruption. The entire Actions runner is ephemeral; local
VM users must delete their VM after the test. Bound subprocesses and readiness
waits, fail the job on assertion failure, and stop the Git server deterministically.

Save a small report containing tested public revision, tool versions, and named
assertion outcomes. Failure diagnostics contain only an allowlist of resource
names, readiness booleans, and condition reasons. Do not upload credentials,
Secret YAML, environment dumps, kubeconfigs, raw subprocess stderr, event messages,
or arbitrary controller logs. Upload only the report directory with short artifact
retention, including on failure.

## Delivery and review

Document the two PR checks, their coverage, local disposable-VM execution, and the
expectation that each future code or infrastructure change includes meaningful
tests. Add a PR template with validation and test-coverage prompts. Do not alter
branch protection or merge automatically. Publish a reviewable PR, attach it to
this chat, and verify its actual GitHub Actions run before claiming completion.

## Sources

- [GitHub pull request workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
- [GitHub-hosted runner privileges](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges)
- [Flux GitRepository authentication](https://fluxcd.io/flux/components/source/gitrepositories/#basic-access-authentication)
- [Git smart HTTP backend](https://git-scm.com/docs/git-http-backend)
