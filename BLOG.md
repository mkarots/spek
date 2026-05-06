# Here is how I built a simple coding agent

`spek` is a side project: a small LLM-powered coding agent that takes a
plain-language description of a program and produces a working, tested
Python package. You write a markdown file describing what you want, point
`spek` at it, and a few minutes later there's an installable package on
disk that builds and passes its own tests.

This post walks through one example end-to-end, then digs into the design
questions I had to answer along the way and what I picked for each.

The full source is at [github.com/&lt;you&gt;/spek](https://github.com/&lt;you&gt;/spek)
(replace with the real link).

## A worked example: the weather converter

Here is the entire input that produces a working CLI tool. It lives at
`examples/weather-tool/SPEC.md`:

```markdown
# Name
Weather-converter

this tool converts weather from one metric system to another.
i.e from fahrenheit to celsius and the opposite

# Input
- --from: string, one of (F, C)
- --to: string, one of (F,C)
- value: positional argument, float, the number of degrees

# Output
The program outputs in stdout the converted value to the desired format

# Implementation
Language: python
```

That is intentionally terse — fewer than twenty lines, with plenty left
unsaid (what should the output look like exactly? what happens if `--from`
and `--to` are the same? what about invalid units?). The agent has to
either fill in those gaps itself or ask the user via the `epistemic` tool.

To turn it into a real package:

```bash
spek build examples/weather-tool/SPEC.md --workdir /tmp/weather
```

A few minutes later, `/tmp/weather` contains a normal Python project —
`pyproject.toml`, source under `src/`, tests under `tests/` — and:

```bash
cd /tmp/weather
uv run pytest                                        # tests pass
uv run weather-converter --from C --to F 0           # 32.0
uv run weather-converter --from F --to C 32          # 0.0
```

That's the whole user experience. The interesting stuff is what happens
between the spec and the package.

## How it works, and why

Before writing any code I had to answer six questions. They look mundane
written down, but each one has at least two reasonable answers, and the
choice shapes everything else. I'll list them first so you can think
about them yourself, then walk through what I picked and why.

1. **What does "specification" actually mean as an input format?** Should
   it be completely free-form, or have some structure?
2. **Which tools do you give the model, and why?**
3. **Where does the agent run?** It's going to execute arbitrary code; that
   has to happen somewhere.
4. **How do you know when the agent is done?** What conditions actually
   mark completion?
5. **What happens if the agent crashes mid-run?** Can we checkpoint and
   resume, or do we start over?
6. **How do you inspect what the agent did?** During a run and after.

### 1. What does "specification" mean?

**The two extremes:**

- *Free-form text.* The user types whatever they want, the LLM figures
  it out. Best UX, lowest learning curve. Worst determinism — the same
  spec on two different runs can produce wildly different programs.
- *Strict schema.* JSON or YAML with required fields, validated up
  front. Maximally reproducible, but now the user has to learn the schema
  before writing their first program, and you keep discovering fields
  the schema doesn't cover.

**What I picked:** markdown with a small set of required headers. A spec
is a markdown file with `# Name`, `# Input`, `# Output`, and
`# Implementation` sections, plus any number of optional `# <key>`
sections for per-parameter detail (you can see all of these in the
weather example above).

**Why:** markdown is the path of least resistance — anyone who has used
GitHub already knows it. The required headers give the LLM something
predictable to anchor every prompt to ("look at the `# Input` section
and propose a CLI signature"), and let me reject malformed specs at
parse time before spending a single token. Extra sections are preserved
verbatim, so the format never gets in the user's way.

If I were starting again I'd add a step where the agent itself can lift
a piece of free-form text *into* this structured form during the clarify
phase. That keeps onboarding zero-effort while still giving the rest of
the pipeline a reliable shape to work with.

### 2. Which tools to give the model?

**The temptation is to give the model everything.** A "Linux command"
tool, a Python REPL, an HTTP client, a package installer, a code formatter,
an AST editor. The more it has, the more it can do, right?

In practice the opposite is true. Each new tool widens the model's
decision tree without necessarily giving it a new capability —
"format the code" and "run `black .`" are the same action, and offering
both just introduces a new way to choose wrong. Too few tools and the
model gets stuck; too many and it spends turns picking between
near-duplicates.

**What I picked:** five tools, total.

| Tool         | What it does                                                                       |
| ------------ | ---------------------------------------------------------------------------------- |
| `read_file`  | Read a file inside the working directory.                                          |
| `write_file` | Create or overwrite a file inside the working directory.                           |
| `grep`       | Search for a string across the working directory.                                  |
| `bash`       | Run a shell command inside the sandbox.                                            |
| `epistemic`  | Pause the run and ask the human a question; the answer comes back as text.        |

The first four are obvious. The last one is the one I'd defend hardest:
`epistemic` turns "I don't know what unit the user wants" from a blocker
into a tool call. The model writes the question, the agent prints it to
the terminal, the user types an answer, and that answer is fed back into
the conversation. It's a tiny primitive but it shows up constantly —
anything genuinely ambiguous in the spec gets resolved this way instead
of guessed.

I also use the SDK's built-in tool-calling support rather than asking the
model to write tool calls as text and parsing them with regex. That's a
small thing in retrospect but it saves you owning a parser for free-form
model output, which is fragile.

### 3. Where does the agent run?

**The choices:**

- *Directly on the host.* Simplest. Also: the model is going to run
  `bash` commands it generated itself. One bad `rm` and your laptop is
  having a worse day than you are.
- *In a VM.* Strong isolation, slow startup, heavy.
- *In a Docker container.* Decent isolation, fast startup, files can be
  shared with the host via bind mounts.

**What I picked:** Docker, and it's required, not optional. Every `bash`
and `write_file` call goes through a long-lived container started when
the agent boots. The host working directory is bind-mounted into the
container so files written inside are immediately visible outside (and
owned by the right user, not root). If Docker isn't running, `spek build`
refuses to start and tells you why — it does not silently fall back to
running on the host.

I'd rather the tool be unusable without Docker than have a "convenient"
mode where a bad command takes out files outside the working directory.

### 4. How do you know when the agent is done?

There are two broad approaches:

- *Trust the model.* Let it end its turn when it thinks it's finished and
  treat that as the stop signal. Simple, and it works most of the time.
- *Verify externally.* Don't take the model's word for it; check some
  deterministic condition about the actual state of the working
  directory.

**What I picked:** verify externally. The agent is "done" when:

1. The configured **build command** has exited 0 in the recent journal, AND
2. The configured **test command** has exited 0 in the recent journal, AND
3. That test run collected **at least one test**.

The third condition is the important one. Without it, an agent that
deletes the test directory satisfies "build green, tests green" — there
just aren't any tests left to fail. With it, the only way to terminate
is to actually have tests that pass.

**Why not trust the model?** Mainly because the cost of a false
positive is high — the user runs `spek build`, the agent reports
success, and they only find out the package doesn't actually work when
they try to use it. A check based on real exit codes from real shell
commands is cheap to write, deterministic, and easy to test. It also
keeps the stop condition in one place that I can change without
re-prompting anything.

### 5. What happens if the agent crashes mid-run?

**The naive answer is "start over."** That's fine for a 30-second run.
For a multi-minute run that has already asked the user three clarifying
questions and produced a plan, it's painful — and worse, the user has to
re-answer questions they already answered, which means the agent might
make different decisions the second time.

**What I picked:** journal everything, replay on resume.

Every meaningful event — every assistant turn, every tool call, every
tool result, every phase transition — is appended as a line to a single
JSONL file (`.spek/journal.jsonl`) inside the working directory.
`fsync` after each write, so a hard crash at any point loses at most the
last entry.

On the next run, `spek` reads the journal, reconstructs the conversation
exactly as it was, figures out which phase it was in from the most
recent phase marker, and continues from there. If a tool call was in
flight when the crash happened (an assistant turn with no matching
result), the call is just replayed. The user's clarifying answers are
in the journal verbatim, so the resumed run sees exactly the same
context.

This paid off the first time the agent crashed: an early bug took it out
right after clarification. I fixed the bug, re-ran the same command, and
it picked up at the start of planning instead of asking every question
again.

### 6. How do you inspect what the agent did?

This is the same problem as resume, viewed from a different angle. If
the agent's full history is durably written somewhere readable, *both*
problems are solved at once.

The journal is plain JSONL — `cat` it, `jq` over it, open it in any
editor. Each line is one event with a kind (`user`, `assistant`,
`tool_result`, `phase`) and the full content. You can see exactly what
the model said, what it tried to do, what came back, and where it went
next.

I added one structural thing on top: the run is split into three named
phases (clarify → plan → execute), and each phase boundary is its own
journal entry. That makes the log easy to skim — "show me what happened
during the plan phase" is a one-liner — and it gave me a place to hang
extra constraints on each phase, like a smaller tool whitelist or a
stricter system prompt. The plan phase, for instance, can only write to
one specific file, which makes the resulting plan a stable artifact the
user can read and even edit before execution starts.

## Internals: what's inside `.spek/`

Most of the agent's state lives in a single hidden directory inside the
working directory. After a successful run, `<workdir>/.spek/` looks like
this:

```
<workdir>/
├── SPEC.md                   # the user's spec (untouched by the agent)
└── .spek/
    ├── journal.jsonl         # full event log
    ├── plan.md               # the plan produced by phase 2
    └── command_config.json   # how to build / test / lint / run the project
```

There is no database, no global state, no `~/.config/spek` — everything
is scoped to the working directory. Wipe `.spek/` and the next run starts
from scratch (`spek build --fresh` does exactly this). Copy the working
directory to another machine and the run is reproducible there.

**`journal.jsonl`** — append-only, one JSON object per line, `fsync`'d
after each write. Each entry has a `kind` (`user`, `assistant`,
`tool_result`, or `phase`) and the full content of that event. This is
both the resume log (replay it to reconstruct the conversation) and the
audit log (read it to see exactly what the model did and why). It's the
single source of truth for the run.

**`plan.md`** — written exactly once, during the plan phase, by the
model itself. It's a numbered checklist of steps the agent intends to
execute. With `--confirm-plan`, the run pauses after this file is
written and waits for user approval — and because it lives on disk as
plain markdown, the user can edit it before approving. During the
execute phase, the model is expected to mark steps `[x]` as it
completes them, so reading `plan.md` during a long run tells you how
far along it is.

**`command_config.json`** — the language-specific commands the agent
should use to operate on the project. For Python it looks like this:

```json
{
  "language": "python",
  "package_name": "weather-converter",
  "build_command": "uv build",
  "test_command": "uv run pytest",
  "lint_command": "uv run ruff check .",
  "format_command": "uv run black --check .",
  "run_command": "uv run weather-converter --help"
}
```

This file is what makes the termination check from question 4 work: the
"build command" and "test command" the journal-walker is looking for
are read from here, not hard-coded. It's also the single place a future
language profile (Node, Go, Rust) would have to populate, so adding
support for a new language is mostly a matter of producing the right
config rather than touching the agent loop.

The pattern across all three files is the same: state is plain text on
disk, scoped to the working directory, readable with standard tools.
That makes the whole agent debuggable with `cat`, `jq`, and `git diff`
— which is what you want at 11pm when something has gone sideways.

## Summary

To recap the questions and the answers:

| Question | Answer |
| --- | --- |
| **Spec format?** | Markdown with a small set of required headers. Reject malformed input before any LLM call. |
| **Which tools?** | Five: `read_file`, `write_file`, `grep`, `bash`, `epistemic`. Small surface area beats a sprawling toolbox. |
| **Where does it run?** | Inside a Docker container, mandatory. No host fallback. |
| **When is it done?** | Build exits 0, tests exit 0, *and* at least one test was actually collected. |
| **What if it crashes?** | Every event is journaled to JSONL with `fsync`; resume just replays it. |
| **How do you inspect a run?** | Read the same journal. Plus three explicit phases for skimmability. |

The thing that surprised me most building this is how much of "the
agent" is just plumbing: a JSON-serialisable journal, a Docker container
with a bind mount, a deterministic exit check, a small tool list. The
LLM is the easy part to integrate. The hard part is giving it a small,
predictable world to act in, and being honest about when it's done.

Source and a runnable example are in the repo:
[github.com/&lt;you&gt;/spek](https://github.com/&lt;you&gt;/spek).
