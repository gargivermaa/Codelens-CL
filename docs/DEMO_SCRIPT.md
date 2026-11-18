# Demo script (8–10 minutes)

## Before the demo (30 minutes earlier)

1. Start Docker Desktop. Then, in `codelens-ci`:
   ```powershell
   docker compose --profile tunnel up --build -d
   ```
   ```powershell
   docker compose ps
   ```
   `api` should be healthy and `worker` up.
2. Warm up the index, so the live review doesn't spend minutes indexing on the free
   tier. Open and merge nothing; simply review one demo PR once in advance
   (`python -m app.cli review <you>/codelens-demo <N> --no-post`), or open a
   throwaway PR.
3. In the browser, open these tabs:
   * the `codelens-demo` repository;
   * the GitHub App's *Advanced → Recent Deliveries* page;
   * http://localhost:8000 (dashboard);
   * the slides.
4. In a terminal window, run `docker compose logs -f worker`.
5. **Backup:** have a screen recording of steps 2–8 below ready
   (Win+Alt+R with Xbox Game Bar, or OBS). If Wi-Fi or Gemini fails live, play it.

## Script

| Time | Show | Say |
|---|---|---|
| 0:00 | Title slide | "Code review tools usually see only the diff. A change can break code in other files that the diff doesn't show. CodeLens CI reviews a pull request *with the repository's own code as context*." |
| 0:45 | Architecture slide | Webhook → queue → worker → Tree-sitter + Gemini embeddings + Qdrant → hybrid retrieval → LangGraph → risk → PostgreSQL → GitHub + dashboard. Everything costs ₹0. |
| 1:45 | GitHub: branch `demo/r02-…` (or `demo/h04-…`) | "This branch adds a function that builds SQL with string concatenation." Open the PR. |
| 2:15 | Recent Deliveries + worker log | "GitHub sends a signed webhook. FastAPI verifies the HMAC, queues the job and answers immediately. The worker picks it up." Point at the log lines: claimed → retrieval → LLM passes → completed. |
| 3:30 | The PR on GitHub | The **CodeLens CI review** summary (risk, issue table) and the inline comment on the exact vulnerable line, with a suggested fix. |
| 4:30 | Dashboard → Pull requests → the PR | The same review from PostgreSQL. **Code diff** tab: the finding on its line. **Risk & pipeline** tab: the risk formula breakdown, LangGraph node timings, model and tokens. |
| 5:45 | Push a second commit to the same PR | "Each commit is reviewed once. A redelivered webhook is ignored, and a new commit gets a new review." |
| 6:30 | Dashboard → Evaluation | Rules vs LLM vs LLM + context on the **holdout** split; confusion matrix; one missed case in the case inspector. "We measure, including our failures." |
| 8:00 | Limitations slide | Python only; synthetic benchmark; the real regression in requests #7433 was missed; free-tier limits. |
| 8:45 | Close | Repeat the problem, the approach and the evidence. Take questions. |

## If something goes wrong live

| Symptom | Fallback |
|---|---|
| No webhook delivery | Recent Deliveries → *Redeliver*. Or run `python -m app.cli review <you>/codelens-demo <N>` to trigger the same pipeline from the terminal |
| Gemini 429 / 503 | The review still completes with rule findings and says the LLM pass failed. Explain the free-tier limits and show the pre-recorded run |
| No internet | Show the dashboard (local) and the recording |
