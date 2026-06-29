"""
Offline interactive runner for generated tax wizards.

Loads a wizard JSON from output/wizards/ and walks the operator through it in the
terminal, following the wizard's own branches. Records each answer and writes a
completed-session report. No network or API key is used.

Usage:
  python run_wizard.py                       # list wizards and pick one
  python run_wizard.py <wizard_id>           # run a specific wizard
  python run_wizard.py <wizard_id> --out report.json
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone

WIZARDS_DIR = os.path.join(os.path.dirname(__file__), "output", "wizards")


def list_wizards() -> list[str]:
    return sorted(glob.glob(os.path.join(WIZARDS_DIR, "*.json")))


def _choose_wizard() -> str | None:
    files = list_wizards()
    if not files:
        print(f"No wizards found in {WIZARDS_DIR}. Run: python generate.py --offline")
        return None
    print("Available wizards:\n")
    for i, f in enumerate(files, 1):
        wid = os.path.splitext(os.path.basename(f))[0]
        print(f"  {i:2}. {wid}")
    raw = input("\nPick a number (or q to quit): ").strip()
    if raw.lower() in ("q", "quit", ""):
        return None
    try:
        return files[int(raw) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return None


def _ask(step: dict) -> str:
    """Prompt for one step and return the chosen option label."""
    options = step.get("options") or ["Done", "N/A", "Needs follow-up"]
    print(f"\n[{step.get('section', '')}]")
    print(f"  {step['question']}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        raw = input("  > ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        # Allow typing the option text directly too.
        for opt in options:
            if raw.lower() == opt.lower():
                return opt
        print("  Please enter a valid option number.")


def _next_step(step: dict, answer: str) -> str | None:
    for branch in step.get("branches", []):
        if branch.get("when", "").lower() == answer.lower():
            return branch.get("go_to")
    # Fall back to the first branch target if none matched the answer label.
    branches = step.get("branches", [])
    return branches[0].get("go_to") if branches else "END"


def run(wizard: dict) -> dict:
    by_id = {s["id"]: s for s in wizard["steps"]}
    print("=" * 70)
    print(wizard["title"])
    print("=" * 70)
    if wizard.get("assumptions"):
        print("\nAssumptions:")
        for a in wizard["assumptions"]:
            print(f"  - {a}")

    answers = []
    current = wizard["steps"][0]["id"]
    guard = 0
    max_steps = len(wizard["steps"]) + 5
    while current and current != "END" and guard < max_steps:
        guard += 1
        step = by_id.get(current)
        if not step:
            break
        answer = _ask(step)
        answers.append({
            "id": step["id"],
            "item_no": step.get("item_no"),
            "section": step.get("section"),
            "question": step["question"],
            "answer": answer,
        })
        current = _next_step(step, answer)

    print("\n" + "=" * 70)
    print("SESSION COMPLETE")
    print("=" * 70)
    counts = {}
    for a in answers:
        counts[a["answer"]] = counts.get(a["answer"], 0) + 1
    for label, n in sorted(counts.items()):
        print(f"  {label}: {n}")

    follow_ups = [a for a in answers if "follow" in a["answer"].lower()]
    if follow_ups:
        print("\nItems needing follow-up:")
        for a in follow_ups:
            print(f"  - [#{a['item_no']}] {a['question'][:90]}")

    print("\nTerminal actions:")
    for ta in wizard.get("terminal_actions", []):
        forms = ", ".join(ta.get("forms", [])) or "-"
        print(f"  - {ta.get('condition', '')}: file {forms}")

    return {
        "wizard_id": wizard["wizard_id"],
        "title": wizard["title"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "answer_counts": counts,
        "answers": answers,
    }


def main():
    parser = argparse.ArgumentParser(description="Run a generated tax wizard offline.")
    parser.add_argument("wizard_id", nargs="?", help="Wizard id (file name without .json)")
    parser.add_argument("--out", help="Write the completed session report to this JSON path")
    args = parser.parse_args()

    if args.wizard_id:
        path = os.path.join(WIZARDS_DIR, f"{args.wizard_id}.json")
        if not os.path.exists(path):
            print(f"Wizard not found: {path}")
            sys.exit(1)
    else:
        path = _choose_wizard()
        if not path:
            return

    with open(path, encoding="utf-8") as f:
        wizard = json.load(f)

    report = run(wizard)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nSaved session report to {args.out}")


if __name__ == "__main__":
    main()
