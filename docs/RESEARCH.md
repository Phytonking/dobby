# Implementation research

Official sources reviewed for this implementation on September 9, 2026. API availability and pricing can change.

| Decision | Evidence |
| --- | --- |
| Gemini Flash-Lite default, model configurable | [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) lists a free tier for `gemini-2.5-flash-lite` and free-tier product-improvement data usage. [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) are project/tier dependent. |
| Structured plans with application validation | [Structured output](https://ai.google.dev/gemini-api/docs/structured-output) supports JSON schema/Pydantic. Syntactically structured output still needs application-level semantic validation. |
| Gateway and early slash defer | [Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding) require an initial response within three seconds; the interaction token remains usable for fifteen minutes. |
| Explicit Message Content intent for context | [Discord Gateway](https://docs.discord.com/developers/events/gateway) documents privileged content access and exceptions for messages mentioning the bot. Reading other recent messages requires enabling the intent. |
| Desktop OAuth with offline access | [Google native-app OAuth](https://developers.google.com/identity/protocols/oauth2/native-app) supports loopback browser authorization and refresh tokens. [Token expiration](https://developers.google.com/identity/protocols/oauth2#expiration) documents testing-mode expiry. |
| User Calendar OAuth instead of sharing service-account credentials | [Calendar insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert) supports `calendar.events`, caller-supplied event IDs, and guest update notifications. |
| Preserve unrelated event data | [Calendar PATCH](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch) leaves omitted fields unchanged; arrays would replace existing arrays, so the bot never emits guest arrays. |
| Pagination and accurate overlap reads | [Calendar list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list) documents page tokens and time bounds; incomplete/empty pages can still have another page. |
| Conditional writes | [Versioned resources](https://developers.google.com/workspace/calendar/api/guides/version-resources) documents ETags and `If-Match` to avoid overwriting concurrent updates. |
| Docker on 64-bit Raspberry Pi OS | [Docker Debian installation](https://docs.docker.com/engine/install/debian/) supports ARM64; Windows uses the same Linux image through [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/). |
| Read-only local secret mounts | [Compose secrets](https://docs.docker.com/reference/compose-file/secrets/) supports file-backed secrets; these are local files, not an encrypted cloud secret store. |
| Multi-architecture publication | [Docker multi-platform GitHub builds](https://docs.docker.com/build/ci/github-actions/multi-platform/) supports AMD64/ARM64 image manifests with Buildx/QEMU. |
| Restart and resource controls | [Compose service reference](https://docs.docker.com/reference/compose-file/services/) documents restart policies, read-only filesystems, security options and resource limits. |

The project now targets local Docker hosting on Windows and Raspberry Pi. The earlier Cloud Run workflow was removed because continuous cloud workers do not meet the user's goal of avoiding hosting bills. Local hosting still requires electricity, connectivity, and compliance with Gemini's API quotas/terms. No real Discord/Google credentials were supplied for live integration testing.
