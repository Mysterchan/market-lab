# Security reporting

Do not post API keys, private market data, account files or exploit-sensitive details
in a public issue. For sensitive reports, use GitHub's private vulnerability reporting
facility if it is enabled for this repository. Otherwise open a minimal issue requesting
a private reporting channel, without the sensitive details. No dedicated private
reporting inbox or response-time commitment is currently advertised.

Imported agent Python and its dependencies execute as trusted local programs unless the
experiment sets `sandbox` (macOS `sandbox-exec`, Linux bubblewrap or rootless Podman),
which confines writes to the Agent's work folder and can deny network access.

CI audits every pinned dependency with pip-audit, runs CodeQL on the Python, JavaScript
and C++ code and publishes a CycloneDX SBOM ([workflow](.github/workflows/security.yml)).

Model calls may transmit prompts/tool results to the configured model provider.
Credentials should be configured through the platform credential interface and must
not be included in source, experiment descriptions, screenshots or public logs.
