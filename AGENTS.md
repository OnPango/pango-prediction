# PANGO Prediction — Agent Instructions

## Repository purpose

`pango-prediction` implements the production prediction service for PANGO.

This repository corresponds to **Route B — Implementation of the PANGO Prediction Module**.

Route A evaluates forecasting models and produces the versioned model policy and, when applicable, a DeepAR checkpoint.

Route B consumes those artifacts and executes forecasting in production.

The central invariant is:

```text
same data semantics
+
same preprocessing
+
same demand classification
+
same model implementation
=
what was evaluated is what gets deployed
```

Before making architectural or domain changes, read:

1. `README.md`
2. `docs/PREDICTION_CONTEXT.md`
3. Any Route A / Route B implementation-plan document present under `docs/`
4. The existing implementation and tests relevant to the requested change

The live repository is authoritative for what has already been implemented.

---

## Architecture

This project uses a pragmatic **Hexagonal Architecture / Ports and Adapters** approach.

Main boundaries:

```text
adapters/inbound
      ↓
application
      ↓
domain
      ↓
ports
      ↓
adapters/outbound
```

Current package structure:

```text
pango_prediction/
├── domain/
├── application/
├── ports/
└── adapters/
    ├── inbound/
    └── outbound/
```

Do not introduce additional architectural layers unless there is a concrete need.

### Domain

`pango_prediction/domain/`

Contains PANGO forecasting concepts and rules.

The domain must not depend on:

- FastAPI
- HTTP
- databases
- Pango Core
- Supabase
- Docker
- cloud providers
- StatsForecast-specific infrastructure
- PyTorch-specific infrastructure

Domain code should be usable directly from:

- production;
- tests;
- Route A experimentation;
- notebooks.

### Application

`pango_prediction/application/`

Contains use-case orchestration.

The forecast application flow is conceptually:

```text
input
↓
series regularization
↓
availability
↓
calendar
↓
demand profile
↓
policy
↓
model port / registry
↓
forecast
↓
result
```

Application code coordinates domain behavior. It should not implement forecasting algorithms or HTTP behavior itself.

### Ports

`pango_prediction/ports/`

Contains interfaces required by the application to communicate with technology-specific components.

For example, forecasting models should eventually be represented through a common port instead of coupling application code directly to StatsForecast or DeepAR.

### Adapters

`pango_prediction/adapters/inbound/`

Contains entry mechanisms such as FastAPI.

`pango_prediction/adapters/outbound/`

Contains integrations such as:

- StatsForecast implementations;
- DeepAR runtime;
- model artifact storage.

FastAPI is an adapter, not the application.

---

## Domain invariants

The forecasting level is:

```text
product × warehouse
```

The series identifier is:

```text
series_id = product_warehouse_id
```

Initial frequency:

```text
weekly
```

Initial forecast horizon:

```text
4 weeks
```

A week is:

```text
Monday → Sunday
```

Route A and Route B must reuse the same implementations of the forecasting domain, especially:

```text
series
availability
calendar
profile
models
```

Do not duplicate these transformations inside notebooks, API routes or adapters.

---

## Data semantics

Demand observations initially enter at daily level.

Series construction is responsible for:

- validating input;
- aggregating duplicate daily demand observations;
- grouping observations into Monday–Sunday weeks;
- filling missing weeks;
- preserving `product_warehouse_id` as `series_id`.

Availability is derived from stock history rather than received as a pre-resolved backend flag.

Availability states are:

```text
AVAILABLE
CENSORED
UNKNOWN
```

Known rules include:

```text
zero sales + zero closing stock
→ CENSORED

positive sales + zero closing stock
→ observed / AVAILABLE

missing reliable stock information
→ UNKNOWN
```

More than 13 consecutive weeks without stock must be detected as out of assortment / no longer supplied according to the project specification.

Do not silently invent business rules that are not yet defined.

In particular, if the exact behavior for made-to-order products or another ambiguous inventory situation has not been specified, identify it as an unresolved domain decision instead of guessing.

---

## Model-selection boundary

Production code does not decide which model is statistically best.

Route A performs model evaluation.

The resulting decision is represented through a versioned:

```text
model_policy.json
```

Route B loads and executes that policy.

During development, a temporary development policy may be used.

Keep these concepts separate:

```text
DemandProfile
=
what the demand behaves like

Policy
=
which model should handle it
```

---

## DeepAR boundary

DeepAR training must never occur inside a forecast HTTP request.

The intended separation is:

```text
offline training
    ↓
checkpoint + metadata
    ↓
private model artifact storage
    ↓
DeepAR inference runtime
```

Do not introduce PyTorch or NeuralForecast into the CPU service until the relevant implementation phase requires them.

---

## Development methodology

Work incrementally.

Prefer one coherent domain capability at a time.

For domain functionality, use a test-first workflow when practical:

```text
1. Define expected behavior
2. Add or update tests
3. Observe the intended failure when useful
4. Implement the smallest correct solution
5. Run tests
6. Run static checks
7. Refactor only with tests passing
```

Do not implement future milestones prematurely.

For example, do not introduce FastAPI while working only on domain series construction.

Do not introduce DeepAR while working on baseline model interfaces.

---

## Teaching and communication requirements

The repository owner is using this project both to build PANGO and to learn backend, software architecture and ML production engineering.

Therefore, when making a meaningful change:

### Before editing

Explain briefly:

1. what is being implemented;
2. where it belongs in Hexagonal Architecture;
3. why it is needed;
4. which files you expect to change.

For a trivial fix, keep this short.

### While implementing

Explain important design decisions as they become relevant.

Do not narrate every line or terminal command.

When introducing a new Python or architectural concept, explain the concept and why it applies here.

Examples:

- dataclasses;
- immutability;
- Protocol;
- dependency inversion;
- adapters;
- ports;
- fixtures;
- parametrized tests;
- dependency injection.

### After editing

Always provide:

1. **Files changed**
2. **What changed in each file**
3. **Why the design was chosen**
4. **Tests/checks executed**
5. **Their results**
6. **Any unresolved decisions**
7. **What the next logical implementation step is**

When useful, show a compact diff or the key new/changed code rather than forcing the user to inspect every file manually.

Never say only "implemented" or "done".

The user must be able to understand what changed.

---

## Repository inspection requirements

Before modifying existing functionality:

1. inspect the relevant files;
2. inspect related tests;
3. check `git status`;
4. preserve existing conventions unless there is a good reason to change them.

Do not assume a file from a previous conversation exists.

Verify the live repository.

Do not overwrite user changes that are unrelated to the current task.

---

## Quality gates

The development environment uses Python 3.12.

The project uses only `pip` and a virtual environment. `uv` is not used. The owner's machine cannot run it, and the Docker image installs with `pip`.

Setup, once per checkout:

```text
python -m venv .venv
```

Activate the environment in every new shell:

```text
Windows (PowerShell):  .venv\Scripts\Activate.ps1
Linux / cloud session: source .venv/bin/activate
```

Install the runtime dependencies and the `dev` group, both read from `pyproject.toml`:

```text
python -c "import subprocess, sys, tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); subprocess.check_call([sys.executable, '-m', 'pip', 'install', *p['project']['dependencies'], *p['dependency-groups']['dev']])"
```

This line works in both PowerShell and bash. It reads the lists from `pyproject.toml`, so that file stays the single source of dependencies, as it is in `docker/Dockerfile.api`. It needs neither a build backend nor a minimum `pip` version.

Current development checks, the same on every platform with the environment active:

```text
python -m pytest
ruff check .
python -m mypy pango_prediction tests
```

In a cloud session, run the setup and the checks yourself before opening a PR. The owner may be on a phone.

CI (`.github/workflows/ci.yml`) runs the same install and the same three checks on every PR into `dev` or `main` and on every push to them. It also builds `docker/Dockerfile.api` and smoke-tests the image:

- it must exit without a service credential;
- with one, it must answer `/health`;
- `/v1/forecasts` must answer 401 without a token and 200 for a fixture request with one.

Running the checks locally first still matters: CI confirms the result, it does not replace it.

A normal implementation task should finish with the relevant checks passing.

If a check fails:

- explain the failure;
- determine whether it is caused by the current change;
- fix failures caused by the current work;
- do not hide or delete tests to obtain a green result.

Use type hints for production Python code.

Prefer explicit, readable code over unnecessary abstraction.

---

## Git workflow

The owner often works from a phone through Claude Code cloud sessions, with no local checkout and no way to run commands.

A cloud container is discarded after a period of inactivity. Anything that has not been pushed is lost, so the remote repository is the only durable record of the work.

### Branches

```text
main            releases; feature work never touches it
dev             integration branch; every feature PR targets it
feature/<slug>  one per feature, created from the latest origin/dev
```

- At the start of a session, run `git fetch origin`, check `git status` and look at the open PRs into `dev`. The session may have started on `main` (which holds only the README) or on an automatic `claude/*` branch. Neither is the starting point for work.
- Start every feature from the latest `dev`:

  ```bash
  git fetch origin dev
  git checkout -b feature/<slug> --no-track origin/dev
  git push -u origin feature/<slug>
  ```

  `--no-track` and the immediate push matter. Without them the new branch
  tracks `origin/dev`, and anything that syncs the checkout, such as the
  "Sync Changes" button of an IDE, pulls `dev` into the branch and pushes the
  branch into `dev`. That happened twice in November 2026 and put unreviewed
  commits on `dev` without a pull request. Check with
  `git for-each-ref --format='%(refname:short) -> %(upstream:short)' refs/heads`
  that a feature branch points at its own remote branch before committing.

- One feature per branch. A feature is one coherent capability that can be reviewed and merged on its own.
- `<slug>` is lowercase and hyphenated, and names the capability: `feature/policy-loader`, `feature/statsforecast-adapters`.
- Cloud sessions are assigned an automatic `claude/*` branch. Do not use it in this repository. The owner authorizes creating and pushing `feature/<slug>` branches instead.
- If the next feature depends on a PR that is not merged into `dev` yet, ask the owner whether to wait for the merge or to branch from the unmerged feature branch. Do not stack branches silently.
- Never push to `dev` or `main` directly. Never force-push a branch someone else has pushed to. Never merge a PR: the owner reviews and merges.

### Commits

- One commit per group of files that change for the same reason. Examples: a domain module together with its unit tests; the API schemas together with the API tests; the documentation for the feature.
- Keep a module and its tests in the same commit, so that every commit passes the checks on its own.
- The subject line is in English, imperative, under about 72 characters, in the style of the existing history (`Add ...`, `Implement ...`). The body lists the files in the commit and why they change together.
- Push after each commit group, so the work survives the loss of the container:

  ```bash
  git push -u origin feature/<slug>
  ```

### Pull request

- Base `dev`, head `feature/<slug>`. Open it once the feature is complete, pushed and passing the quality gates.
- The description follows `.github/pull_request_template.md`: what and why, a table of commits with their files, the checks with their results, open decisions and the next step.
- After opening it, wait for CI on the PR and report its result together with the link. Fix a red CI before asking for a merge, and never get to green by skipping or disabling a check.
- Then give the owner the PR link. The next feature starts on a new branch from `origin/dev`, following the dependency rule above.

### Worklog

Every feature branch adds one entry under `docs/worklog/`, named `YYYY-MM-DD-<slug>.md`, in its last commit before the PR.

The worklog is the in-repository history of what was done and why. Git history and PRs show the what; the worklog keeps the reasoning, the checks and the open decisions next to the code. The format is in `docs/worklog/README.md`.

### Reporting to the owner

The "After editing" requirements above apply. Because the owner reads on a phone, keep each report scannable:

1. branch and PR link;
2. commits, each with its hash and the files it contains;
3. checks executed and their results;
4. what needs the owner: decisions, a review, a merge;
5. the proposed next step.

Prefer short lists and small tables. Do not paste long diffs unless the owner asks for them.

---

## Dependencies

Do not add a dependency merely because it makes a small implementation shorter.

Before adding a runtime dependency:

1. explain why the standard library or existing dependencies are insufficient;
2. state what architectural layer uses it;
3. add it only when the current milestone requires it.

Current philosophy:

```text
domain first
↓
models / ports
↓
application
↓
HTTP
↓
advanced model runtime
↓
deployment infrastructure
```

---

## Current development sequence

Use the live repository to determine exact progress.

The intended high-level sequence is:

```text
Foundation
↓
Domain types
↓
Series
↓
Availability
↓
Calendar
↓
Demand profile
↓
Model port
↓
Baselines
↓
Model registry
↓
Policy
↓
Application ForecastService
↓
HTTP contract
↓
FastAPI adapter
↓
StatsForecast adapters
↓
DeepAR training/runtime
↓
Pango Core integration
↓
Observability / CI/CD / deployment
```

Do not mark a step complete merely because this document lists it.

Verify its implementation and tests.

---

## Source-of-truth rule

When documents and code disagree:

- do not silently choose one;
- identify the discrepancy;
- determine whether it is an implementation bug, outdated documentation or an unresolved decision;
- explain it before making a behavior-changing change.

For forecasting business semantics, preserve the terminology and decisions from the PANGO prediction documentation.

---

## Avoid overengineering

This is a real production-oriented project, but the architecture should remain proportional to the current requirements.

Do not create:

- unnecessary factories;
- generic repositories without persistence needs;
- excessive interfaces;
- dependency-injection frameworks;
- message brokers;
- Kubernetes resources;
- abstractions for hypothetical future providers;

unless the current requirements justify them.

Prefer the simplest implementation that preserves the architectural boundaries and can evolve safely.