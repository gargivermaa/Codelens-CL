# Diagrams

GitHub renders these Mermaid diagrams directly. For slides, paste them into
https://mermaid.live and export PNG/SVG.

## 1. System architecture

```mermaid
flowchart LR
  GH[GitHub<br/>Pull Request] -- signed webhook --> SM[smee.io<br/>tunnel]
  SM --> API[FastAPI<br/>/webhook<br/>HMAC verify]
  API -- enqueue --> R[(Redis)]
  R --> W[Celery worker]
  W -- PR, files, contents --> GHAPI[GitHub REST API<br/>App installation token]
  W -- index base commit --> TS[Tree-sitter<br/>chunker]
  TS --> EMB[Gemini<br/>embeddings]
  EMB --> Q[(Qdrant<br/>repo_&lt;id&gt;)]
  W --> LG[LangGraph<br/>review pipeline]
  LG <-- hybrid retrieval<br/>BM25 + dense + RRF --> Q
  LG -- 2–3 calls --> LLM[Gemini<br/>review model]
  LG --> PG[(PostgreSQL)]
  W -- review + inline comments --> GHAPI
  UI[React dashboard] -- /api/* --> API
  API --> PG
```

## 2. Data flow of one review

```mermaid
flowchart TB
  A[pull_request opened / synchronize] --> B{signature valid?}
  B -- no --> X[401]
  B -- yes --> C[queue job, 200]
  C --> D[claim review for PR + head SHA<br/>skip if already reviewed]
  D --> E[parse diff: added lines,<br/>commentable lines]
  D --> F[index base commit<br/>incremental]
  E --> G[changed functions<br/>from head version]
  G --> H[retrieve related repo code]
  F --> H
  H --> I[bug pass]
  I --> J{security-relevant?<br/>path, added or removed lines,<br/>rule hit}
  J -- yes --> K[security LLM pass + rules]
  J -- no --> L[rules only]
  K --> M[quality pass]
  L --> M
  M --> N[validate against diff<br/>drop unverifiable]
  N --> O[deduplicate, severity, risk score]
  O --> P[(store review, findings,<br/>diffs, retrieval logs)]
  P --> Q[post GitHub review]
```

## 3. Sequence

```mermaid
sequenceDiagram
  participant Dev as Developer
  participant GH as GitHub
  participant API as FastAPI
  participant Q as Redis/Celery
  participant W as Worker
  participant AI as Gemini
  participant DB as PostgreSQL
  Dev->>GH: open pull request
  GH->>API: POST /webhook (X-Hub-Signature-256)
  API->>API: verify HMAC
  API->>Q: process_pr_review.delay(...)
  API-->>GH: 200 queued
  Q->>W: deliver task (acks late)
  W->>GH: GET PR, files, contents (installation token)
  W->>DB: claim (pr_id, head_sha) FOR UPDATE
  W->>AI: embed new/changed chunks (if base moved)
  W->>AI: bug / security / quality passes (JSON schema)
  W->>DB: review + findings + diffs + retrieval logs
  W->>GH: POST review (summary + inline comments)
  W->>DB: status = completed
  Dev->>API: open dashboard
  API->>DB: read review
```

## 4. Database (ER)

```mermaid
erDiagram
  repositories ||--o{ pull_requests : has
  pull_requests ||--o{ reviews : "one per head commit"
  reviews ||--o{ findings : contains
  reviews ||--o{ review_files : "diff of"
  reviews ||--o{ retrieval_logs : logs
  repositories {
    int id PK
    bigint github_id UK
    string full_name
    bigint installation_id
    string default_branch
    string indexed_sha
  }
  pull_requests {
    int id PK
    int repo_id FK
    int pr_number
    string head_sha
    string status
  }
  reviews {
    int id PK
    int pr_id FK
    string head_sha
    string status
    float risk_score
    string risk_label
    text risk_breakdown
    text summary
    bigint github_review_id
  }
  findings {
    int id PK
    int review_id FK
    string fingerprint
    string category
    string severity
    float confidence
    string status
    string file_path
    int start_line
    int comment_line
  }
  review_files {
    int id PK
    int review_id FK
    string path
    text patch
  }
  retrieval_logs {
    int id PK
    int review_id FK
    string strategy
    text retrieved_chunk_ids
    int retrieval_latency_ms
  }
```

Unique constraints: `pull_requests(repo_id, pr_number)`, `reviews(pr_id, head_sha)`
(idempotency), `findings(review_id, fingerprint)`, `review_files(review_id, path)`.
