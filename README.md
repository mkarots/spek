<p align="center">
  <img src="https://raw.githubusercontent.com/mkarots/spek/main/assets/spek.png"
       alt="spek logo"
       width="600">
</p>

# spek

> Turn a `SPEC.md` into an installable, tested Python package.

`spek` is a small LLM-powered coding agent. You write a markdown file
describing what you want, point `spek` at it, and a few minutes later
there's a working Python package on disk that builds and passes its own
tests.

For the design rationale and a worked example end-to-end, see
[`BLOG.md`](./BLOG.md).

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- A running Docker daemon — `spek` runs every shell command and file
  write inside a sandboxed container. If Docker isn't running, `spek
  build` refuses to start.
- An Anthropic API key in the environment (`ANTHROPIC_API_KEY`).

## Install

The PyPI distribution is `spek-cli`; the binary is still `spek`:

```bash
uv tool install spek-cli      # recommended: isolated, on $PATH
# or:
pip install spek-cli
```

Verify:

```bash
spek --help
```

## Configure credentials

`spek` looks for `.env` in the current directory or any ancestor of the
spec file. Copy the template and fill it in:

```bash
cp .env.example .env
$EDITOR .env                  # paste your ANTHROPIC_API_KEY
```

You can also override the model:

```env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6        # optional
ANTHROPIC_BASE_URL=https://api.anthropic.com   # optional, for proxies
```

## Quick start

Two commands:

```bash
spek init my-project                           # scaffold SPEC.md skeleton
$EDITOR my-project/SPEC.md                     # describe what you want
spek build my-project/SPEC.md \
  --workdir my-project --confirm-plan          # run the agent
```

When the run finishes green, `my-project/` contains a normal Python
project (`pyproject.toml`, `src/`, `tests/`) you can use immediately:

```bash
cd my-project
uv run pytest                                  # tests pass
uv run <your-cli> --help                       # the binary you specified
```

### A real example

The repo ships with a worked example at
[`examples/weather-tool/SPEC.md`](./examples/weather-tool/SPEC.md):

```bash
spek build examples/weather-tool/SPEC.md --workdir /tmp/weather
```

A run produces a working `weather-converter` CLI with 27 tests passing.
The full session log and final plan are described in `BLOG.md`.

## CLI

```text
spek --help

usage: spek [-h] {init,build} ...

  init     Scaffold an empty SPEC.md and .spek/.
  build    Run the agent against a SPEC.md spec.
```

Useful `spek build` flags:

| Flag | What it does |
| --- | --- |
| `--workdir DIR`     | (required) Directory for the package; bind-mounted at `/work` inside the container. |
| `--fresh`           | Wipe `.spek/` before starting. Use after significant spec changes so the agent re-plans from scratch. |
| `--confirm-plan`    | Pause after the plan phase and require approval (or edit) of `.spek/plan.md`. Recommended. |
| `--max-steps N`     | Cap on tool calls. Default 120. |
| `--max-seconds N`   | Wall-clock cap. Default 1800. |
| `--quiet`           | Suppress progress output. The journal still records every event. |

## What `spek` writes

Everything is scoped to the working directory. After a successful run:

```
<workdir>/
├── SPEC.md                   # your input (untouched)
├── pyproject.toml            # generated
├── src/...                   # generated
├── tests/...                 # generated
└── .spek/
    ├── journal.jsonl         # full event log (resume + audit)
    ├── plan.md               # the plan (numbered checklist)
    └── command_config.json   # build/test/lint commands the agent uses
```

There is no global state and no `~/.config/spek`. Wipe `.spek/` and the
next run starts from scratch.

## Development

```bash
make install       # uv-managed venv with dev extras
make test          # pytest
make lint          # ruff + black --check
make typecheck     # mypy strict
make build         # sdist + wheel into ./dist
```

Full target list: `make help`.

## License

MIT.
