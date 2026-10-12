# Public readiness verification — 12 October 2026

`LionZXYOrg/infra` remains private. Its encrypted Secrets, SOPS rules, and recovery
bundle now live in the private `LionZXYOrg/infra-secrets` repository, referenced by
the local `secrets/` Git submodule. The production Secret and recovery bundle were
preserved byte for byte. Flux uses an independent source and read-only deploy key.

## Validation

- 21 unit tests passed, including an anchored SOPS encryption/decryption round trip,
  missing-module behavior, bootstrap credential protection, read-only key reuse,
  and mismatched private/public key rejection.
- Public syntax and Kustomize checks passed without private module initialization.
- Recursive module cloning passed using the documented GitHub HTTPS credential helper.
- The existing local age identity matched the moved SOPS rules and decrypted the
  production encrypted Secret, with values suppressed.
- Gitleaks 8.30.1 found no leaks in original remote history or sanitized candidate history.

The clean deployment used a full Ubuntu 24.04.5 ARM64 VM with kernel
`6.8.0-142-generic`, no swap, no host mounts, and no forwarded SSH agent.
The unchanged provisioner installed K3s `v1.35.9+k3s1`; Flux `v2.9.6` was installed
with its release checksum verified. Traefik chart `41.7.1` and cert-manager chart
`v1.21.2` both became ready. Both Git sources, all six Flux Kustomizations, the
self-signed test certificate, and the demo Deployment became ready.

HTTPS at `demo.infra.test` passed certificate verification and returned the expected
demo body. Flux decrypted a disposable encrypted Secret, reconciled its update,
and passed another root reconciliation. No production Cloudflare token, DNS change,
or ACME certificate request was used. The production cluster was left untouched
at the user's request.

Tested infrastructure revision: `6f1da5196696ba5e596ba124146123f5d6a2e82c`.
Tested private fixture update: `69a3cff58ae9542aa51b1801f515e2a8948d5b7f`.
The final bootstrap credential parser was also exercised against the live test
cluster's Flux-generated PKCS8 key. All temporary test refs, deploy keys, VM
instances, and the disposable age identity are removed after verification.
See [the isolated test procedure](../tests/isolated/README.md) for reproduction.
Production DNS/ACME and the workstation SSH wrapper were not tested.

## History and remaining publication boundary

The original 15 commits were rewritten with git-filter-repo 2.47.0 to remove
`secrets/`, `.sops.yaml`, and `bootstrap/sops-age.age`, while preserving useful
history and merge topology. The migration adds only a `160000` Git link at
`secrets`. A private original-history bundle is kept outside the publication clone.
Cutover uses an explicit expected-head lease, followed by a fresh remote clone,
path audit, redacted Gitleaks scan, and public checks.

GitHub retains read-only `refs/pull/1/head` through `refs/pull/4/head` that reference
the original history. The private fork `LionZXYAgent/infra` also retains old
branches. A force push to `master` cannot remove these references or cached views.
Before making this repository public, address the retained pull request references
through GitHub Support and review the fork copies. GitHub documents the required
[sensitive-data cleanup process](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Support eligibility is determined by GitHub; do not assume encrypted files qualify.

There are no GitHub Actions runs, releases, tags, or non-PR issues to clean.
Existing maintainer checkouts must re-clone or reset to the rewritten history;
merging an old branch can reintroduce the removed files. Private local recovery
copies must never be mirrored or pushed into the public repository.
