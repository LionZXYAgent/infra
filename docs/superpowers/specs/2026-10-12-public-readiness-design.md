# Public readiness and private secrets module

## Intent and success criteria

Prepare `LionZXYOrg/infra` for a later visibility change to public. The user approved a private `LionZXYOrg/infra-secrets` Git submodule at `secrets/`, a separate Flux source with a read-only deploy key, preserved but sanitized infrastructure history, and a fresh Ubuntu 24.04 OrbStack deployment with disposable secrets and local TLS. Keep `infra` private during this work. Changing visibility is outside this preparation task.

Success requires an accessible private secrets repository, working bootstrap and secret editing against the module, sanitized branches and tags verified from a fresh remote clone, and a deployment test that provisions K3s from an empty isolated VM and verifies Flux, encrypted-secret decryption, both Helm releases, certificate readiness, and the demo HTTPS response. Record any retained GitHub pull request or fork references that prevent a complete history-cleanliness claim.

## Repository boundary

The private repository contains the current encrypted Secret manifests and `kustomization.yaml` at its root, `.sops.yaml`, and `bootstrap/sops-age.age`. Preserve encrypted file contents and the recovery identity. Adjust the SOPS path rule to paths relative to the private repository. Do not print or commit plaintext credentials. Public scripts, namespace manifests, controllers, applications, and non-secret version pins remain in `infra`.

The public repository contains only a Git link at `secrets/` and `.gitmodules` referring to `LionZXYOrg/infra-secrets`. A public clone without private access must still support syntax, public manifest, and history checks. Commands that read or modify secrets must fail clearly when the private module is missing. A maintainer clones or initializes the module explicitly. Secret changes are committed and pushed in the private repository; the module pointer is updated and committed in `infra`.

Bootstrap loads SOPS rules and the recovery bundle from the private module. Support existing local age identity configuration for noninteractive validation without replacing the recovery identity. Keep the bootstrap guard against the wrong checkout, an unpushed commit, invalid node/IP, and a mismatched existing cluster identity.

## Flux and bootstrap

Add a `GitRepository` named `infra-secrets` in `flux-system`, authenticated by its own Kubernetes Secret and read-only GitHub deploy key. `cluster-secrets` reads path `./` from this source and decrypts with the existing `sops-age` Secret. Namespace creation precedes secret application, and dependent Helm releases and applications retain their existing dependency ordering and deletion behavior.

The main Flux source must not recursively load private submodule content. Bootstrap verifies that the secrets repository is private and that the authenticated maintainer can register its deploy key. Provision its independent credentials before reconciling secrets. A fresh bootstrap supports a public or private infrastructure repository and derives the correct Flux visibility flag from repository metadata. Never change repository visibility implicitly. Repeated bootstrap reuses or validates existing deploy credentials instead of silently granting write access or replacing the SOPS identity.

## History cleanup and cutover

Prepare history in an independent clone because linked worktrees share their Git object database. Keep a private backup outside the candidate publication checkout. Sanitize every advertised infrastructure branch and tag, removing `secrets/`, `.sops.yaml`, and `bootstrap/sops-age.age` from earlier commits before adding the new Git link. Audit all historical paths for additional credential material and scan with a redacting secret scanner. Retain useful commit history and parent relationships when possible. Do not upload private backup refs.

Verify the secrets destination is private and its encrypted files are intact before replacing the old files. Publish the completed migration through the sanitized history with explicit expected-head leases so a concurrent remote update stops the push. Verify advertised refs and their reachable content from a fresh clone after publication. Record old and new heads and restore instructions in a private local report.

GitHub pull request refs and private fork copies can retain removed objects independently of branches and tags. Inspect their contents and ownership. Do not claim that a force push purges GitHub caches or other users' clones. Keep the visibility change pending until retained references are addressed or their implications are explicitly accepted. Do not modify a production cluster during the isolated test.

## Isolated deployment

Create a dedicated Ubuntu 24.04 OrbStack VM with host file sharing disabled, no SSH agent forwarding, bounded resources, and isolated networking. Provision the pinned K3s version through `scripts/provision-server.sh`; do not reuse an existing Kubernetes context or kubeconfig. Install the pinned Flux version.

Use separate temporary Git branches or test repositories for the candidate infrastructure and disposable SOPS fixtures. The fixture uses a newly generated age identity, harmless test values, and a self-signed local certificate for a test-only domain. No production Cloudflare token, production certificate request, or DNS change is needed. Exercise independent private-source authentication and SOPS decryption through Flux. Install the existing Traefik and cert-manager releases, issue local TLS, deploy the demo, and verify the HTTPS body and readiness conditions. Test secret updates and a second reconciliation to demonstrate continued Git synchronization.

Save a redacted validation report with exact versions, test commits, readiness results, and HTTPS result. Remove only VM, deploy keys, and Git references created by this test after collecting results. Preserve production resources.

## Verification and constraints

- Python 3.10+, PyYAML, SOPS, age, GitHub CLI, Flux, and kubectl follow existing project conventions.
- Keep `K3S_VERSION=v1.35.9+k3s1`, `FLUX_VERSION=v2.9.6`, and `SOPS_VERSION=v3.13.3` unless an actual compatibility failure requires a documented change.
- Run the existing bootstrap unit tests and add meaningful coverage for the private-module boundary, public/private visibility, independent deploy-key creation, absent modules, and existing credentials.
- Build public Kustomize manifests from a clone without private module access and encrypted manifests from a maintainer clone.
- Scan both candidate history and final remote history with findings redacted.
- Keep secret values, age identities, GitHub tokens, and kubeconfigs out of console output, public documents, and public history.

## Design alternatives

A separate private checkout avoids Git submodule management but changes the requested local layout and needs extra path configuration. A recursively fetched private submodule inside the main Flux source couples credentials and makes the public source require private access. The approved design uses a local Git submodule and independent Flux sources to keep those concerns explicit.
