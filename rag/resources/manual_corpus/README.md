# Official Vehicle Manual Source Seed

`official_cn_vehicle_manual_sources.csv` and `official_vehicle_manual_sources.csv` are seed lists for testing knowledge ingestion and retrieval against real vehicle owner-manual sources.

The list intentionally stores official manufacturer URLs and selector hints instead of copying manual files into the repo.

## Columns

- `id`: stable source id for download/index logs.
- `brand`, `model`, `year`, `market`, `language`: vehicle metadata.
- `source_type`:
  - `html_manual`: direct official HTML manual URL.
  - `manual_portal`: official manufacturer manual portal plus the model/year selection hint.
  - `manual_portal_cn`: China-market official manufacturer page, service page, app/manual entry, or model page used to locate the user manual.
- `official_url`: manufacturer-owned URL.
- `selector_hint`: what to choose on dynamic portals.

## Current Coverage

- `official_cn_vehicle_manual_sources.csv`: 114 China-market source rows across 40 brands/sub-brands.
- `official_vehicle_manual_sources.csv`: 109 global source rows across 23 brands.
- Tesla direct HTML manuals plus official manual portals for Toyota, Lexus, Honda, Acura, Ford, Lincoln, GM brands, Nissan, Hyundai/Kia/Genesis, Subaru, Mazda, Volkswagen, Audi, BMW, Mercedes-Benz, and Volvo.

On 2026-07-11, a lightweight reachability check of the China-market list returned `106/114` OK. The failures were SSL/timeout issues for a small number of manufacturer domains, not CSV parsing errors.

## Downloaded Artifacts

Downloaded local artifacts live under:

- `downloads/`: official China-market manufacturer entry pages.
  - `114` vehicle rows attempted.
  - `106` rows downloaded successfully.
  - `33` unique HTML artifacts after URL/content de-duplication.
- `downloads_manual_pages/`: second-level pages discovered from downloaded entry pages with links such as `用户手册`, `随车手册`, `车主手册`, or `manual`.
  - `38` discovered manual-link rows.
  - `38` rows downloaded successfully.
  - `12` unique HTML artifacts after de-duplication.
- `downloads_manual_pdfs/`: direct PDF manuals discovered from the manual pages.
  - `12` discovered PDF rows.
  - `12` rows downloaded successfully.
  - `12` unique PDF artifacts after de-duplication.
  - `text/` contains plain-text extracts for fast local search and regression tests.

These artifacts are suitable for initial RAG ingestion and retrieval behavior testing. They are not yet a claim that 100 final per-model PDF owner manuals have been resolved; several domestic OEMs expose manuals through dynamic service pages, apps, mini-programs, or vehicle-specific web apps.

## Usage

For China-market RAG tests, start with `official_cn_vehicle_manual_sources.csv`. Many domestic OEMs publish the user manual through a service page, app/manual center, WeChat mini-program, or vehicle model page instead of a static public PDF.

For quick crawler tests, use `source_type=html_manual` in the global file because those URLs can be fetched as ordinary pages when the manufacturer does not block non-browser clients.

For `manual_portal`, write a brand-specific resolver or manually export the PDF/HTML from the official portal. Keep the final downloaded artifact linked to the original row id.

```bash
python3 scripts/validate_manual_sources.py --max 20
python3 scripts/validate_manual_sources.py --csv resources/manual_corpus/official_cn_vehicle_manual_sources.csv --max 20
python3 scripts/download_manual_sources.py --csv resources/manual_corpus/official_cn_vehicle_manual_sources.csv
python3 scripts/discover_manual_links.py
python3 scripts/download_manual_sources.py --csv resources/manual_corpus/discovered_manual_links.csv --out resources/manual_corpus/downloads_manual_pages
python3 scripts/discover_manual_pdf_links.py
python3 scripts/download_manual_sources.py --csv resources/manual_corpus/discovered_manual_pdfs.csv --out resources/manual_corpus/downloads_manual_pdfs
```

The validator checks URL reachability only; it does not claim that every dynamic portal has been resolved to a final PDF.
