BACKEND_ROOT ?= ../AuroraAgentBackend

.PHONY: setup dev-backend dev-frontend dev-all test-all

setup:
	pnpm setup

dev-backend:
	cd $(BACKEND_ROOT) && uv run --frozen aurora runtime --port 8765

dev-frontend:
	pnpm dev

dev-all:
	pnpm dev

test-all:
	cd $(BACKEND_ROOT) && uv run --frozen pytest
	pnpm lint
	pnpm typecheck
	pnpm test:coverage
