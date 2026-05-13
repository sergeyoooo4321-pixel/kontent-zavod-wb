# Rebuild Notes

The old Content Zavod codebase was intentionally removed from `main` to start a clean rewrite.

Preserved outside Git before cleanup:

- API keys and runtime `.env` files
- model/provider configuration
- prompts and gnome memory
- gnome skills and tools
- selected backend source and tests
- deploy/service references

Rules for the rebuild:

- keep secrets out of Git
- commit only source, sanitized examples, tests, and public docs
- keep generated media/uploads/cache/runtime DBs ignored
- add fresh architecture before restoring production services
