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
| Cloud Run worker pool for continuous Gateway connection | [Worker deployment](https://docs.cloud.google.com/run/docs/deploy-worker-pools) and [manual scaling](https://docs.cloud.google.com/run/docs/configuring/workerpools/manual-scaling) support a continuously running worker without a request-serving application. Requested instances are billed even while idle. |
| Runtime-only secret injection | [Worker secrets](https://docs.cloud.google.com/run/docs/configuring/workerpools/secrets) supports environment references and file mounts with service-account authorization. |
| Short-lived GitHub deployment authentication | [Google GitHub auth action](https://github.com/google-github-actions/auth) and [deployment federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines) describe OIDC federation and recommend numeric repository/owner IDs to resist name reuse. |

The worker-pool choice is an architectural inference from the bot's persistent outbound Gateway connection and Google's worker documentation. Gemini free-tier eligibility does not imply free Cloud Run hosting. No credentials, cloud project, or live Discord server were supplied as part of source-code development.
