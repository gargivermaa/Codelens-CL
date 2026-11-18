# Evaluation methodology and results

The question this evaluation answers is: **does repository-aware context improve
AI code review, and how accurate is CodeLens CI?** Passing unit tests does not
answer it; only labelled data does.

## 1. Datasets (`benchmarks/`)

| Dataset | Size | Splits |
|---|---|---|
| `review_benchmark.json` (v2) | 51 PRs: 38 seeded defects across 13 types, plus 13 clean PRs | dev 11 · validation 12 · holdout 28 |
| `retrieval_benchmark.json` (v2) | 40 queries (code and natural language) with labelled relevant chunks | dev 15 · validation 10 · holdout 15 |
| `sample_repo/shopapp` | 11 modules, 34 code chunks | the code all cases change and retrieve from |

Every review case records:
* id, split, origin (`synthetic`), changed file and change (`replace`/`append`)
* expected defects, each with a code `match` (located to an exact line of the new
  file), category, type, severity, explanation and reference fix

**Defect types:** SQL injection, IDOR, path traversal, command injection, hardcoded
secrets, authentication, authorization, deserialization, SSRF, weak crypto, weak
randomness, logic errors and error handling.

**Rules that keep the numbers honest:**
* **dev** = the original 11 cases. They were inspected during development, and the
  IDOR routing fix was motivated by r03, so they are *not* used for headline claims.
* **validation** is used for tuning (prompts, the RRF sweep).
* **holdout** is never used for tuning; headline numbers come from it. The retrieval
  sweep refuses to run on holdout.
* Labels are written before any model sees a case and are never changed after
  looking at predictions. One label was corrected before any model run: v08's
  `match` hit an unchanged line.
* Severity and type labels of the dev cases were added after development. They are
  reported but not used for claims.

All cases are synthetic, single-file PRs on a small sample app. Two **real-world**
PRs are reported separately (section 5).

## 2. Configurations compared (same cases, same model, same prompts)

| Config | What runs |
|---|---|
| `rules` | 12 deterministic security rules only |
| `llm` | the LangGraph pipeline with Gemini but **no repository context** (retriever disabled) |
| `llm_context` | the full CodeLens pipeline: Gemini + hybrid retrieval over the indexed sample repository |

`llm` vs `llm_context` isolates the effect of repository context.

## 3. Metrics (`app/evaluation/metrics.py`)

Only **confirmed** findings are scored, unless stated otherwise. A prediction
**detects** an expected defect when it is in the same file and its line range is
within ±2 lines of the defect line. A detection is **strict** (fully correct) only
if the category group also matches (security / bug / quality). Each expected defect
can be matched once. A further prediction near it is a *duplicate*; any other
prediction is a *false positive*.

| Metric | Definition |
|---|---|
| Precision | strict TP / predictions |
| Recall | strict TP / expected defects |
| F1 | 2PR / (P + R) |
| Lenient precision / recall | same, with any category accepted |
| False-negative rate | 1 − recall |
| Clean-PR false-positive rate | clean PRs with ≥ 1 finding / clean PRs |
| Category accuracy | strict TP / detected |
| Severity agreement | exact (and within one level) among detected |
| Exact line localization | expected line inside the predicted range, among detected |
| Duplicate rate | duplicate predictions / predictions |
| Confusion matrix | expected group (security, bug, clean) × predicted group (security, bug, quality, nothing) |
| Retrieval | Precision@1, Precision@5, Recall@5, MRR, NDCG@5 (binary relevance) |

**A right issue with the wrong category does not count as a correct detection.**
It appears as "detected" in lenient recall and in the confusion matrix.

## 4. Reproducing

```powershell
python -m app.evaluation.run review --config all --split holdout
```

```powershell
python -m app.evaluation.run review --config llm_context --split holdout --repeats 3
```

```powershell
python -m app.evaluation.run retrieval --split holdout
```

```powershell
python -m app.evaluation.run retrieval-sweep
```

The sweep runs on validation only. Then evaluate the chosen parameters once on
holdout with `--rrf-k` and `--weights`.

Each run writes `benchmarks/results/<run_id>.json` with:
* run id, dataset hash, prompt hash, model and generation config
* per-case predictions, raw model output, dropped findings and errors
* tokens, latency, API failures and attempts

It also writes `<run_id>-cases.csv` and appends a row to `summary.csv`. The
dashboard's **Evaluation** page shows the latest run for each configuration and
split, with case inspection.

Free tier: an `llm_context` holdout run makes about 28 × 2–3 LLM calls plus about
40 embedding calls. Per-minute limits are handled by waiting. Daily limits stop the
run with a clear error, so re-run the next day.

## 5. Results

**Measured (this document is updated only with measured runs):**

| Split | Config | Precision | Recall | F1 | Clean-PR FP | Notes |
|---|---|---|---|---|---|---|
| holdout (v2) | rules | 1.000 | 0.238 (5/21) | 0.385 | 0% | measured 18 Nov, no API needed |
| holdout (v2) | llm | *not yet run* | | | | run on the owner's laptop |
| holdout (v2) | llm_context | *not yet run* | | | | run on the owner's laptop |
| v1 (11 cases) | rules | 1.000 | 0.375 (3/8) | | 0% | earlier benchmark, lenient v1 matching |
| v1 (11 cases) | llm_context, `gemini-3.5-flash-lite` | 0.875 | 0.875 (7/8) | | 0% | earlier benchmark, before the IDOR routing fix |

| Retrieval split | BM25 P@1 / NDCG@5 / MRR | Dense | Hybrid |
|---|---|---|---|
| holdout (v2) | 0.933 / 0.916 / 0.967 | *not yet run* | *not yet run* |
| v1 (15 queries) | 0.933 / – / 0.967 | 0.733 / – / 0.867 | 0.867 / – / 0.933 |

**Real-world PRs** (psf/requests, read-only, nothing posted):

| PR | What it is | Result |
|---|---|---|
| #7502 | correct, merged fix to `_encode_files` | 0 findings: correct (true negative); full index of 342 chunks, hybrid context, 2 LLM passes |
| #7433 | introduced a regression (`isinstance` with a `runtime_checkable` Protocol misses `__getattr__`-based file objects) | missed (false negative), also missed by human reviewers |

## 6. Known issues and observations

* **IDOR (r03), v1:** detected but labelled `bug`, so it counts as a miss under strict
  scoring. Root cause: security routing looked only at added lines, and the PR
  *removed* an authorization check. Removed lines now count. Verify with the v2
  dev split, which contains r03.
* **Discount cap (r04):** the extra "missing percentage cap" finding is a real issue
  the label did not include. It counts as a false positive, and the label was not
  changed.
* **BM25 beat hybrid on the v1 retrieval queries.** The queries are code sharing exact
  identifiers with small target chunks, which favours BM25. v2 adds natural-language
  queries, weighted RRF and a validation sweep. Hybrid is *not* claimed to be better
  unless holdout measurements show it.
* **Subtle runtime-semantics bugs (#7433) are out of reach** for a small free model
  reviewing a diff.
* Synthetic, single-file cases on a small repository overestimate real-world
  performance. The real-world PRs are the reality check.
