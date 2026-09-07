# backlog — gh command reference

All `<placeholders>` come from `.backlog/project-meta.json` unless noted:
`<owner>`, `<repo>` (`owner/name`), `<projectNumber>`, `<projectId>`,
`<fields.*.id>`, `<fields.*.options.*>`.

## One-time auth (writes need the `project` scope)

```bash
gh auth status                     # confirm login
gh auth refresh -s project         # add Projects-v2 write scope (one-time)
```

## Setup — create the board (idempotent; skip a step if it already exists)

```bash
# 1. Create the user-owned Project v2 (prints its number + URL)
gh project create --owner <owner> --title "<Project Title> Backlog"

# 2. Link it to the repo
gh project link <projectNumber> --owner <owner> --repo <repo>

# 3. Add fields (single-select options are comma-separated, order preserved)
gh project field-create <projectNumber> --owner <owner> --name "Status" \
  --data-type SINGLE_SELECT --single-select-options "Inbox,Ready,In progress,Done"
gh project field-create <projectNumber> --owner <owner> --name "Impact" --data-type NUMBER
gh project field-create <projectNumber> --owner <owner> --name "Effort" --data-type NUMBER
gh project field-create <projectNumber> --owner <owner> --name "Score" --data-type NUMBER
gh project field-create <projectNumber> --owner <owner> --name "Boost" --data-type NUMBER
gh project field-create <projectNumber> --owner <owner> --name "Area" \
  --data-type SINGLE_SELECT \
  --single-select-options "feature,bug,infra,docs,research"

# 4. Create the labels on the repo (idempotent: --force updates color/description)
gh label create inbox --repo <repo> --description "Un-triaged capture" --color BFD4F2 --force
# Type labels (at most one per issue)
gh label create bug --repo <repo> --description "Something is broken or behaves incorrectly" --color d73a4a --force
gh label create enhancement --repo <repo> --description "New feature or improvement to existing behavior" --color a2eeef --force
gh label create documentation --repo <repo> --description "Docs, READMEs, comments, guides" --color 0075ca --force
gh label create question --repo <repo> --description "Open question or decision needed, not yet actionable work" --color d876e3 --force
# Area labels (0-2 per issue)
gh label create frontend --repo <repo> --description "UI, components, styling, client/browser behavior" --color fbca04 --force
gh label create backend --repo <repo> --description "Server logic, APIs, data models, services" --color 1d76db --force
gh label create infra --repo <repo> --description "Deployment, hosting, environments, tooling, build" --color c2e0c6 --force
gh label create ci --repo <repo> --description "CI/CD pipelines, workflows, checks" --color bfdadc --force
gh label create security --repo <repo> --description "Vulnerabilities, auth, permissions, hardening" --color ee0701 --force
gh label create performance --repo <repo> --description "Speed, memory, efficiency, scalability" --color f9d0c4 --force
gh label create testing --repo <repo> --description "Test coverage, test infrastructure, flaky tests" --color 0e8a16 --force
gh label create dependencies --repo <repo> --description "Upgrading or managing third-party dependencies" --color 0366d6 --force
# Pipeline labels (auto/human-required mutually exclusive; spec-ready may co-exist; read by dispatchers)
gh label create auto --repo <repo> --description "Suitable for the unattended agents pipeline" --color 5319E7 --force
gh label create human-required --repo <repo> --description "Needs heavy human interaction before automation" --color B60205 --force
gh label create spec-ready --repo <repo> --description "Issue body carries a settled design; the pipeline skips its interview" --color 006B75 --force

# 5. Discover IDs to build .backlog/project-meta.json
gh project view <projectNumber> --owner <owner> --format json   # -> id (projectId)
gh project field-list <projectNumber> --owner <owner> --format json   # -> field + option ids
```

Reconcile note: `gh project field-create` errors if the field already exists —
treat "already exists" as success. Projects that want a different Area taxonomy
edit the option set on the GitHub board and re-run `setup`, which re-discovers the
IDs into `.backlog/project-meta.json`.

## Capture — file an inbox issue

```bash
gh issue create --repo <repo> \
  --title "<concise idea title>" \
  --label inbox --label "<inferred>" [--label "<inferred>" ...] \
  --body "<one loose sentence is fine>"
```

## Triage — promote an inbox issue to a graph node

```bash
# Add issue to the project (prints the item id; capture it)
gh project item-add <projectNumber> --owner <owner> --url <issueUrl>

# Set number fields
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Impact.id> --number <1-5>
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Effort.id> --number <1-5>
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Score.id> --number <round(impact/effort, 1)>
# Optional: manual priority override (default 0; positive boosts above unboosted, negative sinks below)
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Boost.id> --number <n>

# Set single-selects (use the option id from .backlog/project-meta.json)
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Area.id> --single-select-option-id <fields.Area.options[area]>
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Status.id> --single-select-option-id <fields.Status.options.Ready>

# Record blocking edges (append a line; keep any existing body)
gh issue edit <number> --repo <repo> --body "<body>\n\nBlocked by: #<a>, #<b>"

# Drop the inbox label
gh issue edit <number> --repo <repo> --remove-label inbox
# Correct taxonomy labels if the user adjusts them during review
gh issue edit <number> --repo <repo> --add-label "<label>,<label>" --remove-label "<label>"
```

## Sub-issues (native parent/child) — GraphQL, no field IDs needed

```bash
# Resolve issue node IDs (<repoName> is the repo without the owner/ prefix)
gh api graphql -f query='query($owner:String!,$repo:String!,$n:Int!){
  repository(owner:$owner,name:$repo){issue(number:$n){id}}}' \
  -F owner=<owner> -F repo=<repoName> -F n=<parentNumber>

# Attach child to parent
gh api graphql -f query='mutation($parent:ID!,$child:ID!){
  addSubIssue(input:{issueId:$parent,subIssueId:$child}){issue{number}}}' \
  -F parent=<parentNodeId> -F child=<childNodeId>
```

## Land — verify closed + Done (escape hatch; automation normally does this)

```bash
gh issue view <number> --repo <repo> --json state --jq .state
gh project item-list <projectNumber> --owner <owner> --format json \
  --jq '.items[] | select(.content.number==<number>) | {id, status}'
# repair only if needed:
gh issue close <number> --repo <repo>
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Status.id> --single-select-option-id <fields.Status.options.Done>
```

## Start — check-and-claim

```bash
# 1. Item id + live status
gh project item-list <projectNumber> --owner <owner> --format json \
  --jq '.items[] | select(.content.number==<number>) | {id, status}'
# 2. If status == "In progress": abort (already claimed).
# 3. Claim:
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Status.id> --single-select-option-id <fields.Status.options["In progress"]>
# --release: set Ready instead
gh project item-edit --project-id <projectId> --id <itemId> \
  --field-id <fields.Status.id> --single-select-option-id <fields.Status.options.Ready>
```
