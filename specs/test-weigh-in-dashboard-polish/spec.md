# test-weigh-in dashboard polish — Spec

Source: [issue #179](https://github.com/jesdi/general-skills/issues/179).
This extends `specs/test-weigh-in/spec.md`; existing measurement and scoring
contracts still apply. Approved choices: searchable pagination with highest similarity first; map selection
under 200 ms on an agreed benchmark machine; measure HTML size and defer optimization.
See design.md for the test seams and validation protocol.

## Requirements

1. Overlaps remains usable with 6,757 pairs. Test identities remain distinguishable,
   and each similarity value is explicitly labelled as Jaccard similarity. Use
   search across either test identity and pagination, highest similarity first;
   no pair may silently disappear. Show result counts, reset pagination when the
   query changes, and explain an empty result.
2. Map explains grey = 0, amber = 1–3 and green = 4+ unique lines, and that dot
   size represents runtime. Hovering a dot reveals its test name.
3. Selecting a map dot updates its selection and related evidence without rerunning
   the placement calculation or moving dots. Selection must update visible evidence in under 200 ms at 2,172 tests on the
   agreed benchmark machine, without the reported approximately five-second freeze.
4. Map names the selected test and orders its similar tests by descending Jaccard
   similarity, with a label for the numeric values. Existing similarity eligibility
   and highlighting semantics remain in force.
5. Metrics has column headers distinguishing metric, measured value and score, uses
   appropriate units for values, and shows target and bad anchors with units and
   direction from the report's scoring rules.
6. Every unmeasured metric is named with its reason. A pillar with zero measured
   metrics explains this state instead of displaying an empty Metrics table.
7. A report with failing or erroring tests makes their identities available alongside
   the failure summary, consistent with the existing aggregated test outcomes.
8. Tabs have deliberate styling and a visible active state that follows selection.
9. Gauge titles, scores and measurement status align across cards, including cards
   with wrapped titles and a fully measured Fast feedback pillar. Partial-status
   text remains readable, and detail content makes practical use of available width.
10. The report explains that displayed scores are rounded and bounded to 1–99,
    and that the target and bad anchors correspond to scores of 90 and 10. It
    preserves the underlying scores and explains why meeting a target is not 100.
11. All new report interactions work from the self-contained HTML without network
    access, retaining the existing offline contract.
12. Record generated HTML byte size for the real-suite fixture without introducing
    a size cap or removing evidence to shrink the artifact.

## Scenarios

### Scenario: real-suite overlap evidence (R1)

- **Given** 2,172 tests, 3,196 cases and 6,757 overlap pairs, including long node ids
- **When** the user opens Overlaps and navigates its evidence
- **Then** the two identities in each pair and its labelled similarity are distinguishable,
  and every pair remains discoverable through search and pagination, sorted by
  descending similarity; the result count is 6,757 before filtering.

### Scenario: map legend and identity (R2)

- **Given** dots with 0, 1, 3 and 4 unique lines and different runtimes
- **When** the user opens Map and hovers a dot
- **Then** the legend explains all three colour bands and runtime-based size, and
  the hovered test's full identity is available.

### Scenario: repeated selection at scale (R3, R4)

- **Given** a map with 2,172 dots, with one test having 148 similar tests
- **When** the user selects that test and then another test
- **Then** positions remain stable, placement is not recalculated, and the picked
  test's name and related highlights update in under 200 ms for each measured
  selection on the agreed benchmark machine.

### Scenario: sorted similar tests (R4)

- **Given** a selected test whose eligible neighbours arrive in the order 0.91, 1.00, 0.95
- **When** its details appear
- **Then** they appear in the order 1.00, 0.95, 0.91 under a Jaccard similarity label.

### Scenario: interpretable metric values (R5)

- **Given** total runtime 64.16 seconds and branch coverage 94.84 percent
- **When** the user opens their Metrics tabs
- **Then** column headers distinguish values from scores, the values show s and %,
  and runtime anchors show target ≤ 60 s and bad ≥ 600 s while coverage anchors
  show target ≥ 90% and bad ≤ 50%, for the existing version-1 rules.

### Scenario: no measured metrics (R6)

- **Given** a pillar with 0 of 4 metrics measured
- **When** its Metrics tab opens
- **Then** all four metrics are named with reasons they were not measured and the
  report explicitly explains that no metrics were measured.

### Scenario: partially measured metrics (R6)

- **Given** a default run without the mutation tier
- **When** the user opens Regression metrics
- **Then** measured metrics remain visible and the unmeasured mutation metrics
  are identified with the reason that the mutation tier did not run.

### Scenario: failures have identities (R7)

- **Given** test_checkout fails, test_setup errors and test_ok passes
- **When** the user inspects the red-suite summary
- **Then** test_checkout and test_setup are named as affected tests; test_ok is not.

### Scenario: tabs communicate selection (R8)

- **Given** Economy is selected and Metrics is active
- **When** the user switches to Overlaps and then Map
- **Then** the visible active styling follows the selected tab, with only one tab active.

### Scenario: coherent dashboard layout (R9)

- **Given** all five pillars, a wrapped title, one fully measured pillar and one
  partial pillar with 0 of 4 metrics measured
- **When** the report is viewed at desktop and narrow viewport widths
- **Then** corresponding gauge elements align where cards share a row, partial
  statuses remain readable, and the details use available width without clipping
  long identities or leaving the reported unused right-hand region.

### Scenario: score boundary explanation (R10)

- **Given** a raw score of 0.2, a raw score of 100 and zero tests over 10 seconds
- **When** the user inspects displayed scores and their explanation
- **Then** the first two display 1 and 99, the third retains its target-anchor
  score of 90, and the rounding, bounds and anchor mapping are explained.

### Scenario: offline interactions (R11)

- **Given** the generated report opened from disk with networking disabled
- **When** the user changes pillars and tabs, browses overlaps and selects map dots
- **Then** the evidence and explanations remain available without network requests.

### Scenario: search resets a later page (R1)

- **Given** the user is on a later page of the 6,757 overlap pairs
- **When** they search for a test identity that occurs in only one pair
- **Then** that pair appears on the first result page with a matching count of 1.
- **When** they enter a query matching no pair and then clear it
- **Then** an explicit empty state appears, followed by the full searchable set of
  6,757 pairs when the query is cleared.

### Scenario: truthful absence and zero values (R5, R6)

- **Given** one measured metric has value 0 and another is missing without recorded cause
- **When** Metrics opens
- **Then** zero remains a measured value and the missing metric is marked unmeasured
  with its cause unavailable, rather than receiving an invented reason or score.

### Scenario: measure artifact size (R12)

- **Given** the real-suite fixture with 2,172 tests, 3,196 cases and 6,757 overlap pairs
- **When** the standalone report is generated
- **Then** validation evidence records its byte size and fixture identity, without
  imposing a size cap or truncating its evidence.

## Out of scope

Changes to scoring formulas, anchors, measurement semantics or overlap eligibility.
HTML-size optimization is deferred. Measure bytes only; preserve evidence and
the self-contained report. The benchmark machine remains to be agreed before
claiming the latency target has passed.
