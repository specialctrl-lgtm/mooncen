# Crawler payload builder and evidence handoff

This is a dormant, fail-closed contract for a future isolated crawler payload
builder. It does not make Ops Console `build` or `register` available.

## Current boundary

`tools/build_crawler_payload_release.py` accepts only a canonical ticket UUID
filename below a fixed inbox. Repository, output, signing-key, and arbitrary
source paths are not command-line inputs. The ticket format is validated by
`ops_agent/crawler_builder_evidence.py`, but independent Studio source-approval
issuance does not exist yet. A structurally valid ticket is not authorization.

Every successful builder evidence document has `registration_ready=false` and
the complete fail-closed blocker list. The current release agent also does not
enforce the rich payload-tree manifest, and the isolated signer handoff and
isolated test evidence do not exist. Existing release action routing must keep
`build` and `register` unavailable while any of these conditions remains true.

## Commit-only payload contract

The builder reads an exact commit and tree from one standalone,
repository-local Git object database. It rejects alternates, replacement refs,
grafts, shallow state, promisor/partial-clone state, filters, lazy fetches,
linked worktrees, unexpected Git environment overrides, and non-local object
directories. Raw commit, tree, and blob object IDs are recomputed, while the
content manifest records both Git object IDs and SHA-256 digests.

Payload members come only from a reviewed exact-path set plus the pinned
Crawler path-set digest. Paths are canonical ASCII/NFC, traversal and secret
names are rejected, and case-insensitive collisions, symlinks, gitlinks, and
special modes fail closed. Dirty tracked files and untracked worktree files are
never read.

The archive is a deterministic GNU tar stream in a deterministic gzip stream.
Members are byte-ordered, regular files only, have fixed ownership and time,
and are materialized again under a temporary isolated root before evidence is
emitted. The isolated smoke imports the scheduled runner, YAML collector, pull
worker, and parser-probe agent-command closure from that root. It also opens the
six required COLLECTED_YAML seed/candidate inputs through the runtime module,
loads the collected target inventory, and rejects an empty, incomplete, or
duplicate inventory.

The compatible `crawler-release.json` remains the exact schema currently read
by the release agent. Rich commit/tree/blob/member evidence is stored in
`.mooncen-crawler-payload-tree.json` and its detached copy. Registration must
remain disabled until the materializing release agent independently enforces
that exact rich manifest before activation.

## Signer separation

Builder evidence contains digests and immutable identities, never artifact or
repository filesystem paths. The signer evidence contract contains only a
detached public signature, namespace, public key ID, digests, and signing time.
Signing private keys belong only to a future isolated signer and must never be
made readable by the Ops API, builder, database, or release action worker.

## Deferred database handoff

Migration `008` is intentionally not present. Adding a dormant table without
the migration installer, database roles, grants, RLS contract, ticket issuer,
and preflight enforcement would create installation drift without establishing
an authority boundary.

A future change must land those pieces together and prove all of the following:

- an independent source approver is distinct from the author and release actor;
- approved draft revisions, source paths, SHA-256 digests, Git commit, and tree
  are bound into one canonical ticket digest;
- only the isolated builder can consume issued tickets and append builder
  evidence, and only the isolated signer can append public signature evidence;
- rows are immutable, API reads expose no path or secret material, and API
  roles cannot issue tickets, build, sign, register, or mutate evidence;
- release registration verifies source approval, isolated tests, builder
  evidence, signature evidence, and release-agent exact-manifest enforcement.

Until that atomic follow-up is installed and independently verified, source
approval issuance, builder ticket issuance, `build`, and `register` stay false.

## Candidate-tree activation gate

The current builder path-set count and digest pin the reviewed tracked Git
tree, not the dirty workspace. The present workspace also contains crawler
file additions and deletions that are not represented by that pin. Before any
candidate commit can become build-authoritative, an operator must review that
commit's complete `Crawler/**/*.py|yaml` set and update the count and digest in
the same reviewed change. CI must build from that candidate commit and prove
the exact pin. Until then a candidate with a different path set is expected to
fail closed; the builder must never infer a new authority set from the
worktree, index, or untracked files.
