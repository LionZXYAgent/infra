# Home infrastructure

K3s runs the services. Flux applies changes from Git once a minute.
Traefik handles HTTPS. cert-manager renews the certificate for `*.kulikov.uk`.
SOPS encrypts all user-managed secrets with one age key.

## Start or restore the cluster

Use a clean Ubuntu 24.04 or Debian server with a fixed LAN IP, cgroup v2, and no swap.
You need GitHub admin access to both repositories and the password for
`secrets/bootstrap/sops-age.age`, or the matching local SOPS age identity.

On Linux, install the tools as root. For a regular account, add `sudo` before each `apt-get` command.

```bash
apt-get update
apt-get install -y git gh age curl ca-certificates python3 python3-venv openssh-client
```

On a Mac with Homebrew, run `brew install git gh age python` without `sudo`.
Make sure that SSH works from your Mac to the Linux server.
Run the shared steps on the computer that will start bootstrap:

```bash
gh auth login --hostname github.com --git-protocol https --web --scopes repo
gh auth setup-git
gh repo clone LionZXYOrg/infra -- --recurse-submodules
cd infra
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
bash scripts/install-tools.sh
export PATH="$HOME/.local/bin:$PATH"
```

Replace `192.168.1.50` with your server IP. On the Linux server, run:

```bash
SERVER_IP=192.168.1.50 bash scripts/bootstrap.sh
```

From your Mac, use SSH instead. Replace `root` with your SSH user if needed:

```bash
SERVER_IP=192.168.1.50 bash scripts/bootstrap.sh --ssh root@192.168.1.50
```

Enter the recovery password. The script installs K3s and Flux, then restores services and secrets from Git.
For another branch, set `GITHUB_BRANCH` and check out that branch first.
Commit and push all changes in both repositories before bootstrap.

Apply the [K3s network rules](https://docs.k3s.io/installation/requirements#networking). Keep port `6443` private.
For public access, forward TCP ports `80` and `443` to the server.
Point `*.kulikov.uk` to your public IP with Cloudflare DNS only.

```bash
export KUBECONFIG="$HOME/.kube/home-k3s.yaml"
flux get kustomizations -A
kubectl get nodes
```

The kubeconfig file gives you cluster access. To fetch only that file, add `--fetch-kubeconfig` to the bootstrap command.
Do not replace the recovery key during a restore. Back up its password outside Git.
Git restores configuration, not databases or stored files. Back up that data separately.

## Add a service

Create the service under `apps/` with its image, ports, health probes, storage, and HTTPS route.
Add its namespace under `infrastructure/namespaces/` and a Flux Kustomization under `clusters/home/`.
Include both files in their resource lists.
Use `myapp.kulikov.uk` for the route and `tls: {}` for the shared certificate.

If the service needs a secret, update the private module first:

```bash
git submodule update --init secrets
git -C secrets switch main
git -C secrets pull --ff-only
```

```bash
python3 scripts/secretctl.py add myapp myapp-config --prompt API_KEY
```

Use `set` instead of `add` to change an existing secret, and reference its name and
field from the Deployment. Commit and push the private changes before committing
the updated module pointer here. Validate changes with the commands below and
merge into `master`; Flux deploys them automatically.
For errors, run `flux logs --all-namespaces --level=error`.
Restart affected Pods after a Secret change if they read it through environment variables.

## Private secrets module

`secrets/` is a Git submodule of the private [infra-secrets repository](https://github.com/LionZXYOrg/infra-secrets).
It contains encrypted Secret manifests and the recovery bundle.

For an existing checkout, run `git submodule update --init secrets`. After pulling a
new module pointer, run `git submodule update secrets`.
Merge the private Secret PR into `main` before merging its public PR.
Update the public pointer to a commit that belongs to private `main`.
Keep real secret files and recovery material in that private repository.

The required `Secrets main` status blocks pointers outside private `main`.
It also rejects a changed submodule repository, path, or branch.
The audit reads public PR metadata and private commit history. It never runs PR code or reads Secret files.
Its read-only deploy key stays in the `secrets-main` environment, which allows only `master`.
Public validation and deployment tests use no private credentials.

After a private merge, the scheduled audit refreshes open PR statuses every five minutes.
GitHub can delay scheduled jobs. To request an audit immediately, run this command from the public repository:

```bash
gh workflow run secrets-main.yaml --repo LionZXYOrg/infra --ref master
```

## MCP access over the LAN

The MCP endpoint is `https://SERVER_IP:8443/mcp`, with HTTP Basic authentication over HTTPS.
For this cluster, the LAN address is `192.168.1.150`.
The official [Kubernetes MCP chart](https://github.com/containers/kubernetes-mcp-server/tree/v0.0.67/charts/kubernetes-mcp-server) uses the existing read-only account.
Kubernetes blocks writes, Secret reads, and commands in pods.
The server also filters out write tools and blocks Secret resources.

The gateway requires a password for every MCP request.
The backend listens only on the loopback address inside its pod.
Only port `8443` is exposed on `SERVER_IP`.
The endpoint does not use a public domain or the shared Traefik routes.
Do not forward port `8443` through the router. That would expose the endpoint outside the LAN.

Keep credentials in the private `k3s-mcp/mcp-http-auth.sops.yaml` Secret.
Merge the private credentials PR before the public endpoint PR.
On an existing cluster, set the address before merging the public PR.
Run these commands on the workstation with the administrator kubeconfig:

```bash
export KUBECONFIG="$HOME/.kube/home-k3s.yaml"
export SERVER_IP=192.168.1.150
kubectl -n flux-system create configmap cluster-settings \
  --from-literal=SERVER_IP="$SERVER_IP" --dry-run=client -o yaml | kubectl apply -f -
```

New bootstrap runs store their `SERVER_IP` value in this ConfigMap automatically.
If the address or credentials are absent, the endpoint cannot become ready.
Flux installs the server after the account, secrets, and certificate controller are ready.
cert-manager creates a private certificate authority (CA), which signs the certificate for the IP address.
The gateway loads renewed certificates without a restart.

After merging both PRs, wait for the endpoint:

```bash
flux reconcile kustomization flux-system --with-source
kubectl -n flux-system wait kustomization/cluster-mcp-endpoint \
  --for=condition=Ready --timeout=10m
```

On your trusted workstation, install the Python requirements in `.venv`.
Initialize the private module before exporting client credentials.
Use your local SOPS identity to unlock the credentials.
The export command also reads the public CA certificate through your administrator kubeconfig.

```bash
git submodule update --init secrets
.venv/bin/python scripts/mcp_auth.py client --server-ip "$SERVER_IP"
unset KUBECONFIG
```

The command writes `ca.crt`, `curl.conf`, and `opencode.json` under `~/.config/k3s-mcp/`.
The directory uses mode `0700`. Each file uses mode `0600`.
Copy the `mcp` entry from that protected JSON file into your OpenCode configuration.
Start OpenCode with the CA certificate:

```bash
NODE_EXTRA_CA_CERTS="$HOME/.config/k3s-mcp/ca.crt" opencode
```

For another MCP client, use the same URL, Authorization header, and CA certificate.
Keep the exported files outside Git. They contain the password.
Do not disable certificate validation.
To test the connection with curl, run:

```bash
curl --config "$HOME/.config/k3s-mcp/curl.conf" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

For a new installation without the encrypted credential file, run `python3 scripts/mcp_auth.py create` first.
This helper uses `secretctl.py` to encrypt a random password and its authentication hash.
Commit and push the private module before updating its pointer in the public repository.
The Deployment tests use disposable credentials and make sure that unauthorized requests and writes fail.

## Test changes

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

These checks work without private repository access. Every PR also runs a fresh
deployment with disposable secrets and local TLS in GitHub Actions. See the
[isolated deployment procedure](tests/isolated/README.md) to run it in a VM.

## Shared Telegram bot

`apps/telegram-bot-suite` combines Acorn care and Greenwich bin reminders under one Telegram identity.
A gateway holds the existing `acorn-care-bot` token and receives Telegram updates.
Each module runs in its own container with a distinct Linux user ID.
Modules use separate Unix sockets, local connections with kernel-checked caller identities.
Neither module can read the token, another module's socket, or another module's saved records.
The gateway rejects methods, destinations, callback responses, and message changes outside each module's permissions.

Acorn receives messages and button presses only from care topic `210` in chat `-1004424243504`.
The bin module sends notices only to topic `45` and cannot receive updates.
The gateway saves routed updates before it acknowledges them to Telegram.
It retains each module's queue until that module acknowledges its saved offset.
A full queue stops polling rather than discarding addressed updates.
The gateway permits edits, pins, and deletion only for messages that belong to the requesting module.
It exposes no method for webhook changes, bot commands, topic changes, or bulk unpinning.

Each container has a read-only root filesystem, resource limits, separate temporary storage, and no cluster API credential.
A network policy blocks incoming application connections and outgoing connections to private networks.
The pod needs public HTTPS for Telegram and council sources, plus cluster DNS.
Containers share the pod's network and the host kernel. This is not virtual-machine isolation.
A node administrator remains trusted. A gateway compromise can affect every module because the gateway holds the token.

The suite uses three separate retained volumes for care, bin publications, and the gateway's update queue and message ownership.
The care container reuses `acorn-care-bot-state` without a reset or care-state import.
A one-time initialization copies only existing Acorn message receipts and its saved update offset to the gateway.
The initialization mounts care state read-only and refuses to run while the old care process holds its file lease.
Later restarts do not import worker-authored ownership records again.
Care confirmations, portions, health holds, transfers, and existing buttons remain in their current record.

The new Flux object stays suspended, and its Deployment has zero replicas.
The existing Acorn Deployment remains unchanged and responsible for live care.
This preparation does not stop either current reminder service or publish a Telegram message.
There is no separate bin CronJob in this implementation.
Do not activate the separate [legacy binbot implementation](https://github.com/LionZXYOrg/infra/pull/19).

### Greenwich notices

The bin module sends the following Monday-to-Sunday schedule each Monday at noon in `Europe/London`.
Its scheduler accepts only the noon-to-13:00 window, including daylight-saving changes.
It checks gateway access and pin permissions before saving an attempt.
An unavailable gateway creates no attempt and can recover within that window.
After the saved attempt starts the sender, a restart does not retry it.
An unknown send outcome blocks further publications until an operator reviews the saved record.
The rich notice keeps native Telegram headings, date groups, bin lists, and council links.
A compatible Telegram client and the `sendRichMessage` API are required.

Each publication refreshes all five council sources and matches the exact configured property.
The parser uses the published Week A/B calendar and applies holiday revisions once.
It warns about unconfirmed holiday dates instead of guessing.
A failed fetch never uses cached data as a fallback.
A council failure leaves the previous pin and sends at most one fixed alert per week.
After a confirmed send, the module pins the new notice before cleaning up its own previous notice.
Cleanup failure does not block later weeks. Unknown delivery still requires review.
Telegram normally blocks deletion after 48 hours, so older notices can remain after unpinning.

The private module must provide Secret `greenwich-bin-bot-property` in namespace `acorn-care-bot`, key `property.json`.
The JSON object contains `address_label` and `property`.
The `property` object needs string fields `sao`, `pao`, `streetName`, `postTown`, and `Postcode`.
For a property without a subproperty label, use an empty `sao`.
`address_label` joins the house label, street, town, and postcode with ` - `.
The house label joins nonempty `sao` and `pao` with a space.
Use the exact label and fields from the council lookup.
Keep real addresses, postcodes, source responses, tokens, and runtime records outside this public repository.
This preparation does not create that Secret or change the private module pointer.

### Coordinated activation and rollback

Do not enable both care receivers. Duplicate reminders can cause caregivers to issue the same portion twice.
Do not remove any retained volume to retry an uncertain send.
Acorn keeps its approved care plan during this transport change.

1. Add the property Secret through `scripts/secretctl.py` in the private module.
2. Merge the private change and update the public Gitlink to private `main`.
3. Make sure that the tested image is public and supports both cluster architectures.
4. Back up all three volumes outside Git before the cutover.
5. If bin storage already contains old sender records, complete the offline ownership migration below.
6. Stop the old Acorn Deployment through a separate reviewed Git change with `replicas: 0`.
7. Wait until its pod terminates and releases the care volume and polling lease.
8. Disable the existing bin job `d3cf4dc9-a75d-42b2-943c-0e1d6ba7ac36` before the next unpublished target week.
9. Keep the retired duplicate `cb3196b1-dad3-4ea6-82b1-608143c735ad` disabled.
10. Make sure that no old care receiver, bin sender, or unfinished publication remains active.
11. Unsuspend the suite and set its replicas to one through a reviewed Git change.
12. Make sure that gateway and care readiness succeed before testing an existing care button.
13. Before Monday noon, run a bin `preview` and a read-only `verify` through its scoped socket.
14. After the first scheduled notice, make sure that exactly one correct notice is pinned.

If the old bin sender already published the target week, wait for the next unpublished target week before activation.
Before rollback, pause upstream polling without stopping Acorn.
Create `/gateway/private/pause-polling` inside the gateway container as its configured user.
Keep Acorn running until the gateway endpoint `/drainz` returns HTTP 200.
This endpoint requires paused polling, no active upstream poll, and an empty saved update queue.
Acorn must save and acknowledge every queued update before the endpoint succeeds.
If the queue cannot drain, keep both receivers stopped and recover its saved updates offline.
Do not restore the old receiver while acknowledged updates remain only in the gateway queue.

After the queue drains, stop the suite completely before restoring the old care Deployment to one replica.
The old care service reuses the same saved care record and ignores already-processed update IDs.
If a bin delivery is uncertain, review the topic and records before reactivating either bin sender.
Keep the gateway volume and ownership snapshot during rollback. Never reset the saved polling offset.
Before returning to the suite, stop the old receiver and reconcile any updates it processed during rollback.
Do not reuse the old gateway database without that review.
Remove the pause file only after that review, before restarting gateway polling.

### Existing bin storage

The former standalone bin sender used Linux user ID `10001`.
The isolated bin container uses user ID `10002`.
Existing files with mode `0600` need an offline ownership migration before this change.
The volume group alone does not grant access to those files.

Stop all bin senders before migration. A concurrent sender can cause repeated or lost publications.
Back up the volume before changing ownership.
Mount the detached bin volume at `/offline/bin-state` on a trusted maintenance host.
As root on that host, run the repository script with the explicit stopped-worker confirmation:

```bash
python3 apps/telegram-bot-suite/src/migrate_storage.py \
  /offline/bin-state --confirm-workers-stopped
```

The script preserves records and permissions and changes ownership to `10002:10002`.
It rejects unsafe entries and held file locks before changing any ownership.
Do not run this command on the care volume or gateway volume.
The suite does not receive root privileges or an automatic ownership-changing init container.

### Add another module

Add a worker entry to `registry.json` with a unique ID, Linux user ID, socket, chat, topic, and explicit methods.
Add a separate container with its own socket volume, configuration, temporary storage, resource limits, and persistent volume.
Each module can use its own image and dependencies without changing another module's environment.
Mount only that module's socket directory into its container.
Do not mount the Telegram token or another module's volume into a worker.
For a receiving module, use `getUpdates` through `gateway_client.UnixClient` and acknowledge only after saving its effects.
For a sending module, keep `receives_updates: false` and omit polling methods.
Add offline tests for route overlap, permission boundaries, restart recovery, and uncertain delivery before activation.

## Acorn care bot

The [separate bot repository](https://github.com/LionZXYOrg/acorn-care-bot) contains the application and its image build.
The bot sends shared care reminders to one Telegram topic.
It saves each caregiver confirmation before it answers a button press.
The planned meal times are `07:00`, `14:00`, and `19:00` in `Europe/London`.
Wet food uses can fractions. Dry food uses grams.

The owners approved public storage of the chat, topic, caregiver IDs, and recurring care plan.
`apps/acorn-care-bot/runtime.properties` contains the Telegram destination, numeric caregiver IDs, and the fresh-start setting.
`apps/acorn-care-bot/config.json` contains the approved portions, stage dates, caregiver mentions, and `add_full` transfer policy.
The morning time applies from `2026-10-20`. This configuration does not recreate the earlier breakfast on `2026-10-19`.
The Deployment reads these files through a generated ConfigMap, a Kubernetes object for nonsecret configuration.
A change to either file changes the ConfigMap name and the Deployment references.

The owners disabled automatic repeat reminders with `automatic_repeats: false` on `2026-10-20`.
The bot still sends each scheduled checklist once at `07:00`, `14:00`, and `19:00`.
An explicit snooze sends one reminder at its requested time.
The updated image applies this setting to existing state at startup without changing confirmations, portions, holds, or pending transfers.

The owners requested bowl washing and wiping the feeding area once daily, at breakfast.
`bowls_morning_only: true` selects this schedule. Dinner keeps its litter task.
Breakfast keeps litter, bowls, and fresh water. Lunch has no routine cleaning.
This setting requires a bot image that supports the morning-only startup update.
That update preserves completed tasks, historical records, food portions, and explicit one-time transfers.
It cancels only open routine bowl tasks outside breakfast from the activation date onward.
Removing the flag does not restore the previous cleaning schedule.

Keep the bot token in the private secrets module.
Do not add it to either public configuration file.
The application Secret needs only the existing `bot-token` key.
The Deployment mounts that key separately from the public configuration.
Numeric caregiver IDs still control access. A displayed username does not grant access.

The repository and GHCR image are public.
The Deployment pulls the image without registry credentials or `imagePullSecrets`.
Repository visibility does not change package visibility. Configure the GHCR package separately.
The image uses a tested commit tag and an immutable digest for both supported architectures.
The bot needs outbound HTTPS, not a public HTTP route.
Its health endpoints stay inside the cluster.

The Flux object enables one replica with the `Recreate` strategy.
Do not run both reminder services. Duplicate requests can cause caregivers to issue the same portion twice.
Before merging this activation, complete the coordinated cutover:

1. Make sure that the pinned image is available without registry credentials.
2. Make sure that the existing application Secret contains `bot-token`.
3. Give the separate bot access to the configured Telegram group and topic.
4. Make sure that the old care record has no active health-related hold.
5. Stop the old reminder scheduler and any pending reminder jobs before merging the activation.
6. Keep the old care record unchanged as an archive.

If the application Secret is absent, use `python3 scripts/secretctl.py add acorn-care-bot acorn-care-bot --prompt bot-token`.
If `bot-token` already exists, preserve it. No additional application Secret keys are required.
Use a separate bot token. Do not reuse the assistant token or change its Telegram listener.
Commit private changes before updating the public submodule pointer.
Do not read or copy the bot token to test public registry access.

The owners requested a fresh record without importing existing confirmations or queued transfers.
`ACORN_FRESH_START=1` selects this mode for the first start on an empty volume.
The bot records its startup time and creates checklists only for later planned care times.
It does not replay earlier feedings or change the recurring portions and stage dates.
On restart, it keeps that original cutoff and all records that the new bot creates.
This setting never resets an existing volume.

Stop the old care scheduler before activating the new bot.
Keep the old record unchanged as an archive. Do not import its marks, transfers, or Telegram message bindings.
If a later cutover requires history, follow the bot repository's explicit migration procedure instead of the fresh-start mode.
A health-related hold needs separate review before a fresh start. Do not discard an active hold.
The new service saves its record at `/data/state.json` on the `acorn-care-bot-state` volume.
The volume survives Flux pruning and needs a separate backup.

After the activation merges, make sure that Flux applies its revision.
Use `Recreate` so that only one process writes the record.
Do not scale the bot above one replica.
Make sure that `/readyz` succeeds and test one confirmation with the new bot.
If startup fails, stop the new bot before resuming the old scheduler.
A local-path volume stays on its original node. Follow the cluster backup procedure before moving it.

Public tests render the ConfigMap, Secret references, storage, and single-replica activation.
They also exercise configuration-name changes without contacting Telegram or reading credentials.
The disposable deployment test excludes the active production Flux object and rejects any bot workload that starts there.
Prepared applications still enter CI only as suspended Flux objects.
The disposable cluster does not run the bot or use live care records.
A real Telegram confirmation test remains part of activation, not a public CI test.
