# Automatic GitHub → Google Cloud Run deployment

The supplied workflow deploys a **Cloud Run worker pool**, not a request-driven Cloud Run service. It maintains the Discord Gateway connection with one worker and needs no public HTTP endpoint. Worker instances are billed while idle; use a billing-enabled hosting project and set a budget alert. Gemini can use a separate free-tier project.

Complete these one-time steps as the project/repository owner. Commands below are **Bash in Google Cloud Shell** unless labelled PowerShell. No cloud resources are created just by installing or testing this repository locally.

## 1. Prepare a dedicated cloud project and identities

Replace the public identifiers below. `REPO_ID` and `OWNER_ID` are numeric GitHub IDs, obtainable using `gh api repos/OWNER/REPO --jq '.id'` and `gh api users/OWNER --jq '.id'` (the repository API's `.owner.id` works for organizations too).

```bash
export PROJECT_ID='your-google-project'
export REGION='us-central1'
export GITHUB_REPO='YOUR_OWNER/YOUR_REPO'
export REPO_ID='123456789'
export OWNER_ID='1234567'
export ARTIFACT_REPO='calendar-bot'
export WORKER_POOL='discord-calendar'

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com calendar-json.googleapis.com
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

gcloud artifacts repositories create "$ARTIFACT_REPO" --repository-format=docker --location="$REGION"
gcloud iam service-accounts create calendar-runtime --display-name='Discord calendar runtime'
gcloud iam service-accounts create calendar-deployer --display-name='GitHub calendar deployment'
export RUNTIME_SA="calendar-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
export DEPLOY_SA="calendar-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOY_SA" --role=roles/run.developer
gcloud artifacts repositories add-iam-policy-binding "$ARTIFACT_REPO" --location="$REGION" --member="serviceAccount:$DEPLOY_SA" --role=roles/artifactregistry.writer
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --member="serviceAccount:$DEPLOY_SA" --role=roles/iam.serviceAccountUser
```

Use a dedicated project because the deployment identity's Run Developer role is project-scoped. It does not need Owner, Editor, or direct Secret Accessor. The standard Cloud Run service agent must retain its automatically assigned service-agent role to pull same-project images; do not repurpose that identity for the bot.

## 2. Create secrets without putting values in code or shell arguments

```bash
gcloud secrets create discord-token --replication-policy=automatic
gcloud secrets create gemini-api-key --replication-policy=automatic
gcloud secrets create google-calendar-token --replication-policy=automatic

for secret in discord-token gemini-api-key google-calendar-token; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
done
```

In Google Cloud Console → Secret Manager, add a version of `discord-token` containing only the Discord bot token and a version of `gemini-api-key` containing only the Gemini key. Add `google-calendar-token` containing the complete contents of the locally generated `secrets/google-token.json`. Use Console's private secret entry/upload control; never put the values in GitHub issues, Actions variables, or command-line arguments.

Alternatively, with authenticated `gcloud` installed on the local Windows computer, upload the token file directly from PowerShell:

```powershell
gcloud secrets versions add google-calendar-token --project YOUR_PROJECT_ID --data-file=secrets/google-token.json
```

Do not upload `.env`, the entire repository, or the OAuth client file. The token JSON already contains the client information required for refresh. The runtime mounts the token read-only at `/secrets/google-token.json` and keeps refreshed access tokens in memory.

## 3. Trust only your repository's main deployment workflow

Continue in the same Cloud Shell session with the exported variables from step 1:

```bash
gcloud iam workload-identity-pools create github --location=global --display-name='GitHub Actions'

gcloud iam workload-identity-pools providers create-oidc calendar-main \
  --location=global --workload-identity-pool=github \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping='google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id' \
  --attribute-condition="assertion.repository_id == '${REPO_ID}' && assertion.repository_owner_id == '${OWNER_ID}' && assertion.ref == 'refs/heads/main' && assertion.sub == 'repo:${GITHUB_REPO}:environment:production' && assertion.workflow_ref == '${GITHUB_REPO}/.github/workflows/deploy.yml@refs/heads/main'"

gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository_id/${REPO_ID}"

echo "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/calendar-main"
```

The final line prints a public provider resource name, not a secret. Numeric IDs prevent a different repository owner from gaining access by reusing an old repository name. Fork pull requests and other branches do not match this trust condition. If the repo moves or the workflow filename changes, update the condition deliberately.

## 4. Configure GitHub

Create a GitHub Actions environment named **production**, restricted to the `main` branch. To deploy automatically on push, leave deployment reviewers optional; protect `main` through PR review instead. In repository Settings → Secrets and variables → Actions → **Variables**, add:

| Variable | Example/value |
| --- | --- |
| `GCP_PROJECT_ID` | Hosting project ID |
| `GCP_REGION` | `us-central1` (choose a worker-pool-supported region) |
| `GCP_ARTIFACT_REPOSITORY` | `calendar-bot` |
| `GCP_WORKER_POOL` | `discord-calendar` |
| `GCP_RUNTIME_SERVICE_ACCOUNT` | `calendar-runtime@PROJECT_ID.iam.gserviceaccount.com` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `calendar-deployer@PROJECT_ID.iam.gserviceaccount.com` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Public provider name printed in step 3 |
| `DISCORD_GUILD_ID` | Your server ID |
| `ALLOWED_ROLE_IDS` | Scheduler role ID(s), comma-separated |
| `ALLOWED_USER_IDS` | Optional specific user IDs; at least this or roles must be set |
| `ALLOWED_CHANNEL_IDS` | Recommended: scheduling channel IDs only |
| `MENTION_CHANNEL_IDS` | Exact text channel IDs where mentions and context are enabled |
| `GOOGLE_CALENDAR_ID` | Team calendar ID; defaults to `primary` |
| `TEAM_TIMEZONE` | e.g. `America/Denver` |
| `GEMINI_MODEL` | Defaults to `gemini-2.5-flash-lite` |

No Google JSON key, Discord token, Gemini key, or OAuth token goes into GitHub. Calendar IDs and Discord IDs are identifiers rather than authorization credentials; they are passed as runtime configuration.

Protect `main` against force pushes and require passing CI/review. Restrict who can merge workflow/application changes, change Actions variables/environments, or assign scheduler roles. For an established repository, add CODEOWNERS entries naming your actual trusted maintainers for workflows and security-sensitive code. Enable Dependabot and GitHub secret scanning/push protection where supported. Consider pinning reviewed GitHub Actions to commit SHAs as part of your maintenance policy.

**Deployment authority is secret authority:** a maintainer who can deploy malicious code could read the runtime's secrets, even without a direct Secret Accessor role. Only trusted maintainers should have this power. No application can prevent a Google project Owner from changing IAM or reading project secrets.

## 5. Push and verify

Push to `main`. The deploy workflow tests the exact commit, builds without credentials, authenticates with a short-lived GitHub OIDC token, pushes a SHA-tagged image, and calls `gcloud run worker-pools deploy` with one instance and Secret Manager references. It uses the names `discord-token`, `gemini-api-key`, and `google-calendar-token` from step 2.

Stop any local copy before the first deployment. In Actions, verify the deploy job succeeds, then inspect Cloud Run → Worker pools → discord-calendar → Logs for `bot_ready`. Infrastructure deployment success alone does not prove the Discord token or Google OAuth grant is valid.

Run this live smoke test in a dedicated test calendar/channel:

1. As a user without the role, try `/events` and a bot mention. Verify no calendar data is returned and no event is created.
2. With the scheduler role, mention the bot with a future title/date/time. Verify the DM preview defaults to one hour. Cancel it and verify no event exists.
3. Repeat and confirm. Check Google Calendar and `/events` for exactly one event.
4. Provide a 30-minute duration and verify the override.
5. Post two discussion messages with title/date/time, then `@Dobby make this a meeting`. Verify the extracted details and DM privacy.
6. Copy the event ID, rename/reschedule it, and verify unrelated Calendar fields remain intact.
7. Prepare an edit, modify the event directly in Google Calendar, then confirm the old preview. Verify the stale edit is rejected.
8. Prepare a preview, remove the requester’s scheduler role (and any explicit user allowlist access), then try to confirm. Verify access is denied.
9. Delete the test event with confirmation. Try double-clicking a confirmation and verify a single operation.
10. Push a harmless README change to `main`. Verify a new image/revision deploys and the bot reconnects. Pending old previews are expected to expire/fail after replacement.

Tests in CI use mocked external APIs. The local environment used to develop this code did not contain your credentials or a running Docker daemon; the cloud rollout and account integration must be verified with this smoke test.

## Updates, rotation, rollback and shutdown

Normal updates: push to `main`. The workflow serializes deployments and does not cancel a running deployment. A short reconnection window is expected. Do not scale beyond one worker or run multiple copies with the same bot token.

Secrets use `latest`: add a new secret version, then rerun the deploy workflow from `main` to refresh the process. For OAuth expiry/revocation, rerun `scripts/link_google.py` locally and upload its new token file. Revoke compromised Google app grants in the linked Google account; rotate the Discord token and Gemini key in their respective consoles. Disable compromised secret versions after replacement. Changing an allowlist GitHub variable also requires redeployment.

For rollback, revert the faulty commit and push to `main` so all checks rerun. Existing commit-tagged images are also available for a manual worker deployment, but rolling back code does not undo Calendar operations or restore old secret values.

To stop the bot and ongoing worker compute, in Cloud Shell:

```bash
gcloud run worker-pools update discord-calendar --project "$PROJECT_ID" --region "$REGION" --instances=0
```

A later push restores one instance. Disable the deploy workflow as well if you want it to remain stopped. Image storage/Secret Manager charges may remain even with zero workers. Configure Artifact Registry cleanup and billing alerts according to your retention needs.

Official references: [worker deployment](https://docs.cloud.google.com/run/docs/deploy-worker-pools), [worker secrets](https://docs.cloud.google.com/run/docs/configuring/workerpools/secrets), [worker scaling/billing](https://docs.cloud.google.com/run/docs/configuring/workerpools/manual-scaling), [GitHub federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines).
