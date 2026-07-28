# Open Automotive Report Corpus

This source list contains public automotive reports, datasets, policies, and
regulatory releases. It deliberately excludes paywalled analyst reports and
sources that require bypassing technical access controls.

## Download

The existing downloader provides resumable HTTP retrieval, URL/content
deduplication, SHA-256 manifests, and local artifacts:

```bash
python3 scripts/download_manual_sources.py \
  --csv resources/report_corpus/official_report_sources.csv \
  --out resources/report_corpus/downloads \
  --delay 1.0
```

Downloaded PDFs can be text-extracted with the same extraction stage used by
`resources/manual_corpus`, then ingested through the platform RAG pipeline.

## Source Policy

- Keep the official landing page or direct PDF URL in `official_url`.
- Record the publisher, date, topic, and licence/terms before downloading.
- Do not copy paywalled reports or defeat login, CAPTCHA, robots, or rate limits.
- Treat downloadable data tables as structured data sources, not LLM prompts.
