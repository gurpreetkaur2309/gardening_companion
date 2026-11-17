# Catalog data-quality report

| Stage | Records |
|---|---|
| candidates | 6461 |
| extracted | 1652 |
| cleaned | 1038 |
| final | 1002 |

## Rejections

| Stage:reason | Count |
|---|---|
| extract:not_a_species | 2847 |
| extract:not_a_taxon | 1962 |
| clean:no_common_name | 462 |
| enrich:deferred_call_budget_kept_without_care | 218 |
| clean:duplicate_species | 142 |
| enrich:low_confidence_kept_without_care | 19 |
| enrich:not_cultivated | 17 |
| clean:not_plantae | 5 |
| clean:description_too_short | 4 |
| validate:invalid_family | 2 |
| clean:no_binomial_name | 1 |
| enrich:no_ai_answer_kept_without_care | 1 |

## Completeness of the final set

- with image: 99%
- with distinct common name: 100%
- edible: 63%
- with care: 76%

## AI enrichment

- records enriched: 765
- confidence: {'high': 656, 'medium': 109}
- relevance: {'niche': 260, 'common': 505}

## Top families

- Rosaceae: 136
- Fabaceae: 57
- Myrtaceae: 31
- Lamiaceae: 31
- Asteraceae: 30
- Arecaceae: 30
- Apiaceae: 29
- Rutaceae: 24
- Cupressaceae: 24
- Cucurbitaceae: 23
