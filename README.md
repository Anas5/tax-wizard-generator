# Autonomous Tax-Wizard Generator

Converts tax checklists and their controlling tax guidance into structured,
interactive intake wizards (JSON decision trees). It processes every checklist
in the source directory without prompting, resolves ambiguity with documented
assumptions, and writes one validated wizard per checklist plus a manifest.

## How it works

For each checklist the tool: parses it into discrete decision points, pairs it
with the matching guidance (via `mapping.csv` or name-based inference), asks the
LLM to build a branching wizard whose every citation is copied verbatim from the
guidance, validates the result, and writes it to `output/wizards/`. A manifest
tracks every checklist with its status, model, timestamp, and assumption count.

## Install

```bash
pip install -r requirements.txt
```

`anthropic` is optional at runtime. Without it (or without an API key) the tool
still runs, but emits placeholder wizards tagged `[NO_LLM]` that fail validation
on purpose, so they show up in the manifest as `skipped` and can be re-run later.

## Set your API key (required for real wizards)

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

## Quick verification

Confirm the pipeline is wired correctly before any large run. Needs no network:

```bash
python generate.py --self-test
```

Expected: `Self-test PASSED (pipeline ran end to end)`.

## Usage

Put checklists in `input/checklists/` and guidance in `input/guidance/`
(`.md`, `.txt`, `.csv`, `.tsv`, `.json`, `.pdf`). Optionally provide a mapping
file at `input/mapping.csv` with columns `checklist_id,guidance_ref` (see
`input/mapping.csv.example`). Then:

```bash
python generate.py
```

### CLI arguments

| Flag | Default | Purpose |
|------|---------|---------|
| `--checklists` | `./input/checklists/` | Checklist source directory |
| `--guidance` | `./input/guidance/` | Guidance source directory |
| `--mapping` | `./input/mapping.csv` | `checklist_id,guidance_ref` CSV (optional) |
| `--output` | `./output/` | Output directory |
| `--batch-size` | `25` | Checklists processed per batch |
| `--self-test` | off | Run one synthetic checklist and exit |

## Output

- `output/wizards/<wizard_id>.json` — one validated wizard per checklist
- `output/manifest.json` — every checklist with status, model, timestamp,
  assumption count, and a reason for any skip
- `output/run.log` — full run log

Re-running is idempotent: an unchanged checklist (same SHA-256) with its wizard
already on disk is skipped.

## Reviewing a run (important for tax-facing output)

The validator rejects any wizard whose citation does not resolve to the actual
guidance text, so your first keyed run may show a non-zero `validation_failed`
skip count. Those are not lost; they are flagged as not-yet-trustworthy. After a
run, triage in this order:

1. Manifest entries with `status` = `skipped` and `reason` = `validation_failed`
   (the model invented or mismatched a citation).
2. Successful wizards (`status` = `finished`) with the highest `assumption_count`
   (where guidance was thin and the model had to infer).

Wizards generated without the LLM are tagged `[NO_LLM]`; re-run them once the
API is reachable.

## Wizard schema

```json
{
  "wizard_id": "slug",
  "checklist_source": "file/id",
  "title": "Human title",
  "assumptions": ["[ASSUMPTION] ...", "[INFERRED] ..."],
  "steps": [
    {
      "id": "q1",
      "question": "...",
      "type": "yes_no | single_select | multi_select | number | date | text",
      "options": ["..."],
      "guidance_ref": "verbatim citation from the guidance, or [INFERRED] reason",
      "branches": [
        {"when": "answer condition", "go_to": "q2 | END", "action": "form/doc/result or null"}
      ]
    }
  ],
  "terminal_actions": [
    {"condition": "path summary", "forms": ["..."], "documents": ["..."], "notes": "..."}
  ]
}
```

## File layout

```
tax-wizard-generator/
├── generate.py        CLI entry point, batching, manifest, --self-test
├── builder.py         prompt construction + validate_wizard (the safety gate)
├── llm.py             Anthropic call w/ guarded import, retry/backoff, fallback
├── ingest.py          multi-format loaders, hashing, mapping
├── requirements.txt
├── README.md
├── input/
│   ├── checklists/    your checklists go here
│   ├── guidance/      your guidance goes here
│   └── mapping.csv.example
└── output/            wizards/, manifest.json, run.log (generated)
```
