# Makefile — developer-facing entry points for `spek`.
#
# All targets are thin wrappers over `uv` so behaviour matches local
# development, CI, and the agent's own sandbox. Running `make` with no
# argument prints this help.
#
# Publishing flow (read before your first release):
#
#   1. Bump `version` in pyproject.toml and commit.
#   2. Tag the commit:        git tag v$(make _print-version) && git push --tags
#   3. Smoke-test on TestPyPI: make publish-test
#   4. Verify install:        uv pip install -i https://test.pypi.org/simple/ spek-cli
#   5. Publish to real PyPI:  make publish
#
# `publish` refuses to run unless the working tree is clean, the tag
# matches the pyproject version, and lint+typecheck+tests are green.
# That's deliberate — once a version is uploaded to PyPI it can't be
# re-used (you can yank it, but you can't replace it).

PYTHON_VERSION ?= 3.11
PACKAGE        := spek
DIST_DIR       := dist

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help (auto-generated from `## ` comments next to target names)
# ---------------------------------------------------------------------------

.PHONY: help
help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make <target>\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

.PHONY: install
install:  ## Install runtime + dev dependencies into a uv-managed venv.
	uv sync --extra dev

.PHONY: format
format:  ## Auto-format the codebase (black + ruff --fix).
	uv run black .
	uv run ruff check --fix .

.PHONY: lint
lint:  ## Run ruff and black in check mode.
	uv run ruff check .
	uv run black --check .

.PHONY: typecheck
typecheck:  ## Run mypy in strict mode.
	uv run mypy src

.PHONY: test
test:  ## Run the test suite.
	uv run pytest

.PHONY: test-cov
test-cov:  ## Run tests with coverage and emit term + xml reports.
	uv run pytest --cov=$(PACKAGE) --cov-report=term-missing --cov-report=xml

.PHONY: run
run:  ## Print spek's CLI help (smoke-check the entry point).
	uv run spek --help

# ---------------------------------------------------------------------------
# Build / publish
# ---------------------------------------------------------------------------

.PHONY: clean
clean:  ## Remove build artefacts, caches, and the dist directory.
	rm -rf $(DIST_DIR) build *.egg-info
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml htmlcov
	# `-prune` keeps `find` from descending into pycache dirs after deleting them
	# (avoids "No such file or directory" warnings on macOS).
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: build
build: clean  ## Build sdist + wheel into ./dist.
	uv build
	@echo
	@echo "Built artefacts:"
	@ls -1 $(DIST_DIR)

# Internal: print the version declared in pyproject.toml. Used by
# `check-tag` and the help text above.
.PHONY: _print-version
_print-version:
	@uv run python -c "import tomllib, pathlib; \
	print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])"

# Internal: refuse to publish unless the working tree is clean and the
# current HEAD has a tag of the form v<pyproject-version>. This catches
# the most common pre-flight mistake: bumping the version but forgetting
# to tag, or tagging the wrong commit.
.PHONY: check-tag
check-tag:
	@status=$$(git status --porcelain); \
	if [ -n "$$status" ]; then \
		echo "refusing to publish: working tree is dirty"; \
		git status --short; \
		exit 1; \
	fi
	@version=$$($(MAKE) -s _print-version); \
	tag="v$$version"; \
	if ! git rev-parse -q --verify "refs/tags/$$tag" >/dev/null; then \
		echo "refusing to publish: no git tag $$tag for the current pyproject version"; \
		echo "  run: git tag $$tag && git push --tags"; \
		exit 1; \
	fi; \
	if [ "$$(git rev-list -n1 $$tag)" != "$$(git rev-parse HEAD)" ]; then \
		echo "refusing to publish: tag $$tag does not point at HEAD"; \
		exit 1; \
	fi; \
	echo "ok: HEAD == $$tag"

.PHONY: publish-test
publish-test: lint typecheck test build  ## Upload to TestPyPI (no tag check; for rehearsal).
	@if [ -z "$$UV_PUBLISH_TOKEN" ] && [ -z "$$UV_PUBLISH_PASSWORD" ]; then \
		echo "set UV_PUBLISH_TOKEN (or UV_PUBLISH_USERNAME/PASSWORD) before running"; \
		exit 1; \
	fi
	uv publish --publish-url https://test.pypi.org/legacy/ $(DIST_DIR)/*

.PHONY: publish
publish: check-tag lint typecheck test build  ## Upload to PyPI. Requires a clean tree and matching git tag.
	@if [ -z "$$UV_PUBLISH_TOKEN" ] && [ -z "$$UV_PUBLISH_PASSWORD" ]; then \
		echo "set UV_PUBLISH_TOKEN (or UV_PUBLISH_USERNAME/PASSWORD) before running"; \
		exit 1; \
	fi
	uv publish $(DIST_DIR)/*
