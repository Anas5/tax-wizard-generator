import json
import re
import logging
from typing import Dict, Any, Optional

from llm import call_llm

logger = logging.getLogger(__name__)

SCHEMA_PROMPT = """
OUTPUT SCHEMA (per wizard, JSON)
{
  "wizard_id": "<slug-of-checklist-name>",
  "checklist_source": "<file/id>",
  "title": "<human title>",
  "assumptions": ["..."],
  "steps": [
    {
      "id": "q1",
      "question": "...",
      "type": "yes_no | single_select | multi_select | number | date | text",
      "options": ["..."],
      "guidance_ref": "<pub/section/line copied verbatim from the GUIDANCE CONTENT>",
      "branches": [
        {"when": "<answer condition>", "go_to": "q2 | END", "action": "<form/doc/result or null>"}
      ]
    }
  ],
  "terminal_actions": [
    {"condition": "<path summary>", "forms": ["..."], "documents": ["..."], "notes": "..."}
  ]
}

RULES:
1. Parse the checklist into discrete decision points.
2. Order questions so disqualifying/gating questions come first.
3. Determine branching logic.
4. guidance_ref MUST be a short string that actually appears in the GUIDANCE CONTENT
   above (a section number, pub number, or a distinctive phrase copied verbatim).
   Do NOT invent section numbers. If no guidance covers a step, set guidance_ref
   to "[INFERRED] <reason>" and add a matching [INFERRED] note to assumptions.
5. EVERY branch must terminate in an action or another existing step id via `go_to`.
6. No orphan nodes. Every `go_to` must be "END" or an id that exists in steps.
7. At least one branch must reach "END", and there must be at least one terminal_action.
8. Resolve all gaps via assumption (add to "assumptions" and tag inline [ASSUMPTION]).
9. If a field is missing, infer it and tag [INFERRED].
10. Output ONLY valid JSON matching the schema. No preamble, no markdown fences.
"""


def generate_wizard(checklist_id: str, checklist_content: str, guidance_content: str) -> Dict[str, Any]:
    """Generates a wizard from checklist and guidance using the LLM."""
    prompt = f"""
    CHECKLIST ID: {checklist_id}

    CHECKLIST CONTENT:
    {checklist_content}

    ---

    GUIDANCE CONTENT:
    {guidance_content}

    ---

    {SCHEMA_PROMPT}
    """

    response_text = call_llm(prompt)

    try:
        wizard_data = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(
            f"Failed to parse LLM output as JSON for {checklist_id}: {e}\n"
            f"Response snippet: {response_text[:200]}"
        )
        from llm import deterministic_fallback
        wizard_data = json.loads(deterministic_fallback(prompt, error=f"JSONDecodeError: {e}"))

    # Normalize identity fields. Strip the file extension from the slug so ids
    # read as "eitc" rather than "eitc-md".
    base = re.sub(r"\.[a-z0-9]+$", "", checklist_id.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "wizard"
    if not wizard_data.get("wizard_id") or wizard_data.get("wizard_id") == "fallback-wizard":
        wizard_data["wizard_id"] = slug
    if not wizard_data.get("checklist_source") or wizard_data.get("checklist_source") == "unknown":
        wizard_data["checklist_source"] = checklist_id

    return wizard_data


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def validate_wizard(wizard: Dict[str, Any], guidance_content: Optional[str] = None) -> bool:
    """
    Validates a wizard against the structural and guidance-integrity rules.

    Checks:
      - steps exist and each has a non-empty guidance_ref
      - every branch go_to is "END" or an existing step id (no orphans)
      - at least one path reaches END and at least one terminal_action exists
      - every step is reachable from the first step (no dead/unreachable nodes)
      - guidance_ref resolves: it either appears in guidance_content or is
        explicitly tagged [INFERRED]/[NO_LLM]. A [NO_LLM] placeholder fails so
        it is never mistaken for a real wizard.
    """
    try:
        wid = wizard.get("wizard_id", "<unknown>")
        steps = wizard.get("steps", [])
        if not steps:
            logger.error(f"Wizard {wid} failed: no steps.")
            return False

        step_ids = {s.get("id") for s in steps}
        norm_guidance = _normalize(guidance_content) if guidance_content else ""

        reaches_end = False
        is_placeholder = False

        for step in steps:
            sid = step.get("id")
            ref = step.get("guidance_ref", "")

            if not ref:
                logger.error(f"Wizard {wid} failed: missing guidance_ref on step {sid}.")
                return False

            if "[NO_LLM]" in ref:
                is_placeholder = True

            # guidance_ref must resolve: tagged as inferred/no-llm, OR actually
            # present in the guidance text. This is the tax-safety gate.
            tagged = any(t in ref for t in ("[INFERRED]", "[NO_LLM]", "[ASSUMPTION]"))
            if not tagged and norm_guidance:
                if _normalize(ref) not in norm_guidance:
                    logger.error(
                        f"Wizard {wid} failed: guidance_ref '{ref}' on step {sid} "
                        f"does not resolve to the guidance text and is not tagged [INFERRED]."
                    )
                    return False

            for branch in step.get("branches", []):
                go_to = branch.get("go_to")
                if go_to == "END":
                    reaches_end = True
                elif go_to not in step_ids:
                    logger.error(
                        f"Wizard {wid} failed: orphan branch go_to '{go_to}' from step {sid}."
                    )
                    return False

        if is_placeholder:
            logger.error(f"Wizard {wid} failed: contains [NO_LLM] placeholder; needs re-run.")
            return False

        if not reaches_end:
            logger.error(f"Wizard {wid} failed: no branch reaches END (no terminal path).")
            return False

        if not wizard.get("terminal_actions"):
            logger.error(f"Wizard {wid} failed: no terminal_actions defined.")
            return False

        # Reachability: walk from the first step; flag any step nothing points to.
        first = steps[0].get("id")
        reachable = {first}
        frontier = [first]
        by_id = {s.get("id"): s for s in steps}
        while frontier:
            cur = frontier.pop()
            for branch in by_id.get(cur, {}).get("branches", []):
                nxt = branch.get("go_to")
                if nxt and nxt != "END" and nxt not in reachable:
                    reachable.add(nxt)
                    frontier.append(nxt)
        unreachable = step_ids - reachable
        if unreachable:
            logger.error(f"Wizard {wid} failed: unreachable steps {sorted(unreachable)}.")
            return False

        return True

    except Exception as e:
        logger.error(f"Wizard validation encountered an error: {e}")
        return False
