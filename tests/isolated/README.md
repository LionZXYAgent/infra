# Isolated deployment validation

These fixtures deploy the real namespaces, Traefik and cert-manager Helm releases,
and demo application. They use an independent private Git source, a disposable
SOPS identity, and a self-signed certificate for `demo.infra.test`. They exclude
production ACME issuers, Cloudflare credentials, and production certificates.

Use a fresh Ubuntu 24.04 server with no swap, or a full Lima VZ VM on macOS.
`lima.yaml` disables host mounts, SSH agent forwarding, and automatic port forwarding.
OrbStack Linux machines share kernel swap devices and fail the provisioner's
no-swap check; the production guard remains unchanged.

## Procedure

1. Install Lima and the pinned repository tools. Set `LIMA_HOME` to a private
   temporary directory, then run `limactl start --name infra-public-test --tty=false tests/isolated/lima.yaml`.
   Verify `/proc/swaps` has no devices and get the VM's IPv4 address with
   `limactl shell --workdir /tmp infra-public-test ip -o -4 addr show`.
2. Copy `scripts/provision-server.sh` into the VM with `limactl copy`. Run it
   through `sudo` with the VM address, node name `isolated-1`, the value of
   `K3S_VERSION`, and a new private output path such as `/root/isolated-kubeconfig`.
   All subsequent Kubernetes commands must name that kubeconfig explicitly.
3. Archive this repository's tested commit with `git archive` and copy the archive
   into the VM. Extract it and run `scripts/install-tools.sh flux` as root.
   Install Flux with the pinned `FLUX_VERSION`, then wait for its four Deployments.
4. Publish the tested, sanitized infrastructure commit to a new temporary branch
   named `codex/isolated-validation`. Check first that this branch is absent.
   Keep both GitHub repositories private throughout this test.
5. In a private temporary directory, generate a fresh age identity and SOPS rules
   targeting only that identity. Create an orphan branch with a root Kustomization
   and one encrypted `v1/Secret`, named `isolated-probe` in namespace `demo`, with
   a harmless `probe` field. Publish it as `codex/isolated-validation` in
   `infra-secrets`. Include no production files or recovery identity in this branch.
   Pass an explicit SOPS configuration and filename override when encrypting;
   otherwise SOPS may discover the production checkout's configuration.
6. Generate separate Git authentication Secrets named `flux-system` and
   `infra-secrets`, using `flux create secret git --ssh-key-algorithm=ed25519 --export`.
   Capture the output privately, register only each public key as a read-only
   deploy key on its matching repository, and apply the Secrets through stdin.
   `bootstrap.ensure_secrets_access()` implements the private-source operation;
   call it twice and verify that the second call reuses the existing key.
   Record the two deploy key IDs for cleanup.
7. Apply the disposable identity as `flux-system/sops-age` through stdin. Create
   `flux-system` GitRepository pointing at the infrastructure test branch and a
   Flux Kustomization with `spec.path: ./tests/isolated`. Do not bootstrap `clusters/home`.
8. Wait for both GitRepositories, all six Flux Kustomizations, both HelmReleases,
   `traefik/isolated-tls`, and `demo/demo` to be ready. Compare the decrypted probe
   to the expected harmless value without displaying Secret contents.
9. Save the issued certificate's public `tls.crt` to a VM-local CA file. Run curl
   inside the VM with `--cacert`, `--resolve demo.infra.test:443:VM_IP`, and
   `https://demo.infra.test/`. Require the page body to contain
   `K3s + Flux + GitHub works.`; do not use `--insecure`.
10. Change the harmless encrypted probe in the private test branch, commit and
    push it, then run `flux reconcile kustomization cluster-secrets --with-source`
    using the VM kubeconfig. Verify the changed value and another successful
    root reconciliation. Record tested Git commits, versions, and readiness.
11. Delete only the two recorded test deploy keys and the two temporary branches,
    using expected-head leases for branch deletion. Delete the test VM and
    disposable identity. Keep the deployment report free of credentials.

Build the fixtures locally with `kubectl kustomize tests/isolated` and
`kubectl kustomize tests/isolated/demo`. This test covers clean server provisioning,
private source authentication, SOPS, controllers, local TLS, and the application.
It does not exercise production DNS/ACME issuance or the workstation SSH wrapper.
