# Results

170 concepts, 68 revealed per persona, 5 seeds. Mean ± sd over seeds; each seed pools all 5 personas.

## Headline

| Method | AUC (all) | AUC (boundary areas only) | Accuracy |
|---|---|---|---|
| prior_zipf | 0.69 ± 0.02 | 0.73 ± 0.02 | 0.67 ± 0.02 |
| knn_term[bge-base-en-v1.5] | 0.76 ± 0.01 | 0.72 ± 0.02 | 0.68 ± 0.01 |
| knn_gloss[bge-base-en-v1.5] | 0.79 ± 0.01 | 0.72 ± 0.02 | 0.72 ± 0.01 |
| knn_term[all-MiniLM-L6-v2] | 0.74 ± 0.01 | 0.70 ± 0.01 | 0.68 ± 0.01 |
| knn_gloss[all-MiniLM-L6-v2] | 0.75 ± 0.02 | 0.68 ± 0.03 | 0.69 ± 0.02 |
| knn+prior | 0.82 ± 0.02 | 0.78 ± 0.02 | 0.75 ± 0.02 |
| jev_prior (no map) | 0.77 ± 0.01 | 0.84 ± 0.01 | 0.66 ± 0.01 |
| jev | 0.91 ± 0.01 | 0.89 ± 0.01 | 0.69 ± 0.02 |
| jev, cut learned on map | 0.91 ± 0.01 | 0.89 ± 0.01 | 0.82 ± 0.01 |
| jev, neutral wording | 0.91 ± 0.01 | 0.90 ± 0.02 | 0.73 ± 0.03 |
| llm[ling-3.0-flash] | 0.82 | 0.81 | 0.72 |

*Boundary areas* are the ones where the persona knows some but not all of the area, so area alone cannot answer and depth has to.

## Accuracy by case

| Method | cold area (knows none of it) | fully known area | boundary area: known (at/below) | boundary area: one step too deep | boundary area: 2+ steps too deep |
|---|---|---|---|---|---|
| prior_zipf | 0.79 ± 0.05 | 0.45 ± 0.05 | 0.62 ± 0.08 | 0.61 ± 0.05 | 0.77 ± 0.05 |
| knn_term[bge-base-en-v1.5] | 0.93 ± 0.07 | 0.71 ± 0.11 | 0.69 ± 0.03 | 0.44 ± 0.04 | 0.75 ± 0.06 |
| knn_gloss[bge-base-en-v1.5] | 0.98 ± 0.02 | 0.96 ± 0.03 | 0.53 ± 0.03 | 0.65 ± 0.04 | 0.87 ± 0.03 |
| knn_term[all-MiniLM-L6-v2] | 0.87 ± 0.07 | 0.68 ± 0.14 | 0.72 ± 0.03 | 0.47 ± 0.05 | 0.68 ± 0.03 |
| knn_gloss[all-MiniLM-L6-v2] | 0.95 ± 0.04 | 0.91 ± 0.05 | 0.55 ± 0.04 | 0.56 ± 0.05 | 0.82 ± 0.03 |
| knn+prior | 0.93 ± 0.02 | 0.90 ± 0.05 | 0.65 ± 0.04 | 0.66 ± 0.03 | 0.86 ± 0.03 |
| jev_prior (no map) | 0.34 ± 0.02 | 0.48 ± 0.05 | 0.99 ± 0.01 | 0.30 ± 0.04 | 0.69 ± 0.02 |
| jev | 0.99 ± 0.01 | 0.24 ± 0.03 | 0.24 ± 0.06 | 0.99 ± 0.01 | 1.00 ± 0.00 |
| jev, cut learned on map | 0.93 ± 0.05 | 0.83 ± 0.05 | 0.77 ± 0.02 | 0.68 ± 0.06 | 0.94 ± 0.01 |
| jev, neutral wording | 0.98 ± 0.01 | 0.40 ± 0.07 | 0.34 ± 0.07 | 0.98 ± 0.01 | 1.00 ± 0.00 |
| llm[ling-3.0-flash] | 0.73 | 0.73 | 0.85 | 0.45 | 0.77 |

## AUC by persona (pooled over seeds)

| Method | backend | frontend | junior | ml_engineer | hobby_cook |
|---|---|---|---|---|---|
| prior_zipf | 0.72 | 0.66 | 0.76 | 0.78 | 0.55 |
| knn_term[bge-base-en-v1.5] | 0.81 | 0.73 | 0.68 | 0.69 | 0.78 |
| knn_gloss[bge-base-en-v1.5] | 0.76 | 0.76 | 0.69 | 0.71 | 0.87 |
| knn_term[all-MiniLM-L6-v2] | 0.80 | 0.65 | 0.67 | 0.66 | 0.80 |
| knn_gloss[all-MiniLM-L6-v2] | 0.71 | 0.67 | 0.66 | 0.67 | 0.90 |
| knn+prior | 0.78 | 0.78 | 0.77 | 0.80 | 0.88 |
| jev_prior (no map) | 0.94 | 0.83 | 0.84 | 0.75 | 0.51 |
| jev | 0.89 | 0.91 | 0.88 | 0.89 | 0.94 |
| jev, cut learned on map | 0.89 | 0.91 | 0.88 | 0.89 | 0.94 |
| jev, neutral wording | 0.90 | 0.92 | 0.88 | 0.89 | 0.94 |
| llm[ling-3.0-flash] | 0.89 | 0.79 | 0.85 | 0.84 | 0.75 |

## Probes: P(knows) for hand-picked cases

*loo*: everything else revealed. *shallow*: also hides every concept in the same area at the target's depth or deeper, so the map only shows what sits above it.

| Map | Persona | Concept | Truth | knn_term[bge-base-en-v1.5] | knn_gloss[bge-base-en-v1.5] | knn_term[all-MiniLM-L6-v2] | knn_gloss[all-MiniLM-L6-v2] | jev |
|---|---|---|---|---|---|---|---|---|
| loo | backend | MVCC | doesn't | 0.10 | 0.21 | 0.46 | 0.26 | 0.03 |
| loo | backend | Postgres | knows | 0.96 | 0.90 | 0.77 | 1.00 | 0.45 |
| loo | backend | write skew | doesn't | 0.12 | 0.30 | 0.28 | 0.22 | 0.02 |
| loo | backend | Maillard reaction | doesn't | 0.16 | 0.09 | 0.21 | 0.10 | 0.04 |
| loo | frontend | hydration | knows | 0.63 | 1.00 | 0.74 | 0.85 | 0.16 |
| loo | frontend | Kubernetes | doesn't | 0.52 | 0.08 | 0.38 | 0.10 | 0.00 |
| loo | ml_engineer | KV cache | knows | 0.85 | 0.68 | 0.98 | 0.83 | 0.27 |
| loo | ml_engineer | React Fiber | doesn't | 0.10 | 0.08 | 0.00 | 0.06 | 0.00 |
| loo | junior | closure | doesn't | 0.45 | 0.65 | 0.10 | 0.94 | 0.08 |
| loo | hobby_cook | sourdough starter | knows | 0.93 | 0.97 | 0.40 | 1.00 | 0.12 |
| loo | hobby_cook | git | doesn't | 0.00 | 0.00 | 0.00 | 0.00 | 0.26 |
| shallow | backend | MVCC | doesn't | 0.10 | 0.50 | 0.46 | 0.54 | 0.14 |
| shallow | backend | Postgres | knows | 1.00 | 0.56 | 0.84 | 0.90 | 0.06 |
| shallow | backend | write skew | doesn't | 0.12 | 0.48 | 0.28 | 0.45 | 0.04 |
| shallow | backend | Maillard reaction | doesn't | 0.18 | 0.45 | 0.63 | 0.12 | 0.07 |
| shallow | frontend | hydration | knows | 0.63 | 0.91 | 0.74 | 0.84 | 0.08 |
| shallow | frontend | Kubernetes | doesn't | 0.61 | 0.21 | 0.79 | 0.11 | 0.00 |
| shallow | ml_engineer | KV cache | knows | 0.85 | 0.68 | 0.98 | 0.83 | 0.17 |
| shallow | ml_engineer | React Fiber | doesn't | 0.10 | 0.20 | 0.00 | 0.06 | 0.00 |
| shallow | junior | closure | doesn't | 0.50 | 0.77 | 0.16 | 0.99 | 0.15 |
| shallow | hobby_cook | sourdough starter | knows | 0.93 | 0.56 | 0.25 | 0.84 | 0.02 |
| shallow | hobby_cook | git | doesn't | 0.10 | 0.09 | 0.00 | 0.00 | 0.01 |

## Cost and latency

- **jev**: 2550 calls, median 0.29s, mean $0.000063 per prediction
- **llm**: 510 calls, median 7.17s, mean $0.000090 per prediction
- **embeddings**: 0.0s to load models and embed all 170 concepts twice per model (0s when cached); kNN itself is microseconds.

## Clustering: do areas and domains fall out of the embeddings?

| Embedding | ARI vs 11 areas | ARI vs 2 domains | cooking cluster purity | clusters holding cooking |
|---|---|---|---|---|
| term[bge-base-en-v1.5] | 0.02 | 0.06 | 0.71 | 5 |
| gloss[bge-base-en-v1.5] | 0.31 | 1.00 | 1.00 | 2 |
| term[all-MiniLM-L6-v2] | 0.22 | -0.03 | 0.36 | 2 |
| gloss[all-MiniLM-L6-v2] | 0.40 | 1.00 | 1.00 | 1 |
