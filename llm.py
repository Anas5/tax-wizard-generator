import os
import json
import time
import random
import logging

# Guarded import: the tool must run even when the SDK is not installed.
# A missing package now degrades to the deterministic fallback instead of
# crashing the whole program at startup.
try:
    from anthropic import Anthropic
    _ANTHROPIC_AVAILABLE = True
except Exception:  # ImportError or any transitive import failure
    Anthropic = None
    _ANTHROPIC_AVAILABLE = False

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192          # raised from 4096 so large multi-branch wizards don't truncate
MAX_RETRIES = 5            # transient errors (429/529/timeouts) retry before falling back
BASE_DELAY = 2.0           # seconds; exponential backoff with jitter

SYSTEM_PROMPT = (
    "You are an autonomous tax-wizard generator. You convert tax guidance and "
    "tax checklists into structured, interactive intake wizards. You never ask "
    "the operator clarifying questions. When information is ambiguous, you make "
    "a documented assumption, flag it inline as [ASSUMPTION], and continue. "
    "Output ONLY valid JSON matching the exact schema requested, with no "
    "preamble and no markdown fences."
)

# HTTP status codes worth retrying. 429 = rate limit, 5xx = server-side/overload.
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status in _RETRYABLE_STATUS:
        return True
    name = type(error).__name__.lower()
    return any(k in name for k in ("ratelimit", "overloaded", "timeout", "connection", "apistatus"))


def _strip_fences(content: str) -> str:
    if "```json" in content:
        return content.split("```json")[1].split("```")[0].strip()
    if "```" in content:
        return content.split("```")[1].split("```")[0].strip()
    return content.strip()


def call_llm(prompt: str) -> str:
    """
    Calls the Anthropic LLM with retry/backoff on transient errors.
    Falls back to a deterministic parser if the SDK is missing, no key is set,
    or all retries are exhausted. Fallback output is tagged so degraded wizards
    are findable in the manifest.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not _ANTHROPIC_AVAILABLE:
        logger.warning("anthropic SDK not installed. Using fallback parser.")
        return deterministic_fallback(prompt, error="anthropic SDK not installed")

    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. Using fallback parser.")
        return deterministic_fallback(prompt)

    client = Anthropic(api_key=api_key)
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            # Concatenate all text blocks rather than assuming a single block.
            parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
            content = "".join(parts) if parts else (response.content[0].text if response.content else "")

            # Guard against silent truncation producing invalid JSON downstream.
            if getattr(response, "stop_reason", None) == "max_tokens":
                logger.warning("LLM response hit max_tokens; wizard may be truncated.")

            return _strip_fences(content)

        except Exception as e:
            last_error = str(e)
            if _is_retryable(e) and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    f"LLM call failed (attempt {attempt}/{MAX_RETRIES}, retryable): "
                    f"{e}. Retrying in {delay:.1f}s."
                )
                time.sleep(delay)
                continue
            logger.error(f"LLM call failed (attempt {attempt}/{MAX_RETRIES}, giving up): {e}")
            break

    return deterministic_fallback(prompt, error=last_error)


def deterministic_fallback(prompt: str, error: str = "") -> str:
    """
    Minimal deterministic fallback when the LLM is unavailable or fails.
    Tags output with [NO_LLM] so these wizards are findable in the manifest
    and can be re-run once the API is reachable.
    """
    assumptions = ["[NO_LLM] Generated via fallback parser; re-run with API for full branching."]
    if error:
        assumptions.append(f"[LLM_FAILED] {error}")

    fallback_schema = {
        "wizard_id": "fallback-wizard",
        "checklist_source": "unknown",
        "title": "Fallback Generated Wizard",
        "assumptions": assumptions,
        "steps": [
            {
                "id": "q1",
                "question": "Placeholder generated without LLM. Re-run required. [NO_LLM]",
                "type": "yes_no",
                "options": ["Yes", "No"],
                "guidance_ref": "[NO_LLM] unresolved",
                "branches": [
                    {"when": "Yes", "go_to": "END", "action": "Re-run with API key"},
                    {"when": "No", "go_to": "END", "action": "Re-run with API key"},
                ],
            }
        ],
        "terminal_actions": [
            {"condition": "Yes", "forms": [], "documents": [], "notes": "[NO_LLM] placeholder."},
            {"condition": "No", "forms": [], "documents": [], "notes": "[NO_LLM] placeholder."},
        ],
    }
    return json.dumps(fallback_schema, indent=2)
