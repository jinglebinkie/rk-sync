# RK-Sync Kubernetes Deployment Plan

We will containerize and deploy your `rk-sync` Google Drive to Runkeeper background worker into your `k3s-on-pi` cluster. Like `couponvault`, this will involve creating a generic Helm Chart in the app's repository, and then deploying that chart using Flux (with Kustomization and HelmRelease) and Sealed Secrets on the cluster.

## Review of the Application structure
- The script `sync-gdrive-to-runkeeper.,py` (note the typo in the comma, which we might want to fix or at least configure correctly in Dockerfile; currently `dockerfile` copies `sync_worker.py` which might mean you already renamed it or the user was about to? Actually `dockerfile` references `sync_worker.py` but `sync-gdrive-to-runkeeper.,py` is the file you have. I plan to rename it appropriately to `sync_worker.py` or fix the Dockerfile to match).
- The worker requires the following Environment Variables: `DRIVE_FOLDER_ID`, `RUNKEEPER_EMAIL`, `RUNKEEPER_PASS`, `POLL_INTERVAL`.
- The worker uses a database `sync_history.db` mapped at `/data`, so we need a PersistentVolumeClaim (PVC).
- The worker expects `/app/secrets/token.json` to authenticate with Google Drive, which we'll map from a Kubernetes Secret.
- Since it acts as a daemon with a while loop (no HTTP API), we do NOT need an Ingress or a Service layer like `coupon-tracker` does.

## User Review Required

> [!WARNING]  
> The `rk-sync` directory currently has the python script named `sync-gdrive-to-runkeeper.,py`. However, the `dockerfile` mentions `COPY sync_worker.py .` and `CMD ["python", "sync_worker.py"]`. 
> Do you want me to rename your script to `sync_worker.py` to match the Dockerfile, and also standardize `dockerfile` to `Dockerfile`?

> [!IMPORTANT]
> The Helm Chart for `rk-sync` will be simple (no Service/Ingress), but how will the docker image be built and pushed? Do you have an automated process (e.g. GitHub Actions) or will you push it to `moppie/rk-sync` on Docker Hub manually first?

## Proposed Changes

### 1. `rk-sync` Application & Helm Chart

#### [MODIFY] [rk-sync](/Users/marcroelofs/work/github/prive/rk-sync)

We will clean up the script name and generate a formal Helm chart.

- **`Dockerfile`**: Rename from `dockerfile` and ensure it targets `sync_worker.py`.
- **`sync_worker.py`**: Rename from `sync-gdrive-to-runkeeper.,py`.
- **`charts/rk-sync/Chart.yaml`**: The Helm chart definition.
- **`charts/rk-sync/values.yaml`**: Expose variables like `image`, `driveFolderId`, `pollInterval`, `persistence.size`, and the names of the existing secrets storing Runkeeper credentials and `token.json`.
- **`charts/rk-sync/templates/deployment.yaml`**: A standard Kubernetes Deployment manifesting your Python image. It will mount the `/data` directory using the PVC and map the `/app/secrets/token.json` via the Secret.
- **`charts/rk-sync/templates/pvc.yaml`**: Generates a PersistentVolumeClaim to persist SQLite across pod restarts.

---

### 2. `k3s-on-pi` Cluster Configuration

#### [NEW] [k3s-on-pi apps/rk-sync](/Users/marcroelofs/work/github/prive/mine/clusters/k3s-on-pi/apps/rk-sync)

We recreate the structure you used for `couponvault`.

- **`kustomization.yaml`**: The standard entrypoint declaring resources (`helm-release.yaml` and secrets).
- **`helm-release.yaml`**: The Flux object deploying the Helm chart, configuring `.Values` (e.g., `nfs-client` storageClass for the PVC, repository URL, poll intervals).
- **`helm-repository-oci.yaml`**: Required if you manage your charts on an OCI registry using Flux.
- **`secrets/secret-rk-sync-template.yaml`**: Since we use Sealed Secrets, I'll provide the plaintext template detailing the structure. You can encode your values (Runkeeper Email/Pass, Base64 Token, Drive Folder ID) into it locally, then kubeseal it to `sealedsecret-rk-sync.yaml`.

## Open Questions

- Where do you store the `token.json` currently? Our template for the `SealedSecret` will give you a location to place its base64 encoded content.
- Do you have an OCI registry set up for your charts like `moppie-oci` that was used in CouponVault, or do you want to keep the chart just in the repository? (I will assume we'll use `moppie-oci` for now, but to do so you might have to package and push the helm chart).

## Verification Plan

### Manual Verification
1. Fix up `rk-sync` code/dockerfile, test building the `rk-sync` image locally to confirm Playwright and the python logic build successfully.
2. Push your `rk-sync` image and chart (if using OCI).
3. The cluster creates the `rk-sync` namespace and deployment via Flux.
4. Verify logs (`kubectl logs -n rk-sync deployment/rk-sync`) confirm connection to Google Drive and Runkeeper using the secrets.
