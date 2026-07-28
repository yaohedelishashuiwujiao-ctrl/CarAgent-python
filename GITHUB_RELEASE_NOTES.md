# GitHub Release Notes

This public repository contains the complete application source code organized by architecture: frontend, backend, Agent Runtime, Agentic RAG, vision service, scripts, tests, database schema, documentation, and evaluation harness.

Large local assets are intentionally excluded from git:

- Raw PDF/HTML/TXT corpus payloads under `rag/resources/**/artifacts`, `documents`, and text extraction folders.
- Local model weight files under `models`.
- `.env.local`, runtime logs, caches, backups, and `node_modules`.

The repository keeps corpus manifests, indexing/evaluation scripts, infrastructure config, and RAG documentation. Full local RAG data can be rebuilt or mounted before running `rag_service.rebuild()`.
