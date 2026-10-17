# Fork image and Kubernetes

The `build.yml` workflow publishes `ghcr.io/enespl/downtify` on main pushes, version tags and manual runs. It uses GitHub's automatic token; Docker Hub credentials are not needed. Both linux/amd64 and linux/arm64 are built. Before publication, CI boots the amd64 image as UID 1000 and checks health, recognition capabilities, AI Guard policy, ffmpeg and fpcalc.

Use the immutable `sha-<full main commit>` tag or digest printed in the workflow summary. The image includes both AMV recognition and AI Music Guard. Recognition credentials are optional; metadata recognition works without them.

For the existing ArgoCD arrstack deployment:

1. Replace the upstream container image with the published fork image.
2. Add optional secretKeyRef environment variables:
   - `DOWNTIFY_ACOUSTID_KEY`: Secret `downtify-recognition`, key `ACOUSTID_API_KEY`.
   - `DOWNTIFY_AUDD_TOKEN`: the same Secret, key `AUDD_API_TOKEN`.
3. Keep `/data` and `/downloads` mounts. Recognition and Guard tables are initialized in the existing data directory. Keep one replica and the Recreate strategy.
4. Add a startup probe on `/api/health` (periodSeconds 5, failureThreshold 60, timeoutSeconds 5).
5. Sync the arrstack Application in ArgoCD and check rollout status.

Create the AcoustID secret without putting its value in Git or shell history:

```bash
read -rsp 'AcoustID application key: ' acoustid_key; echo
printf %s "$acoustid_key" | kubectl -n arrstack create secret generic downtify-recognition \
  --from-file=ACOUSTID_API_KEY=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -
unset acoustid_key
kubectl -n arrstack rollout restart deployment/downtify
kubectl -n arrstack rollout status deployment/downtify --timeout=300s
```

This command creates an AcoustID-only Secret. If it already contains an AudD token, preserve that key when updating the Secret. Environment variables are read at pod startup: restart after credential changes.

A new GHCR package may be private. For anonymous pulls, set the package visibility to Public in GitHub package settings. Otherwise configure a Kubernetes imagePullSecret with GHCR read permission and reference it in the pod spec. Never commit registry tokens or recognition keys.

To roll back, revert the ArgoCD image change and sync again. Back up `/data` before deployment using the existing PVC backup procedure.
