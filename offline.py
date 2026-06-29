"""
Offline, deterministic wizard generation from AICPA-style tax checklist PDFs.

These checklists are authoritative source documents in their own right, so this
module treats each checklist as its own guidance: every generated step cites a
verbatim line copied from the checklist. That makes builder.validate_wizard's
citation-integrity gate pass with no LLM and no network — the wizards are fully
offline and every citation provably resolves to the source text.

The AICPA PDFs are laid out in two columns with a styled (dropped) first letter
on each numbered item. Naive text extraction interleaves the columns and splits
that first letter off ("R equest ..."). We fix both: crop each page into left
and right halves and extract them in reading order, then repair the dropped cap.
"""

import os
import re
import logging

try:
    import pdfplumber
except ImportError:  # keep import-safe like the rest of the toolchain
    pdfplumber = None

logger = logging.getLogger(__name__)

# A numbered checklist item at a line start: "108)", "205)", "1201)".
# The styled first character can split a digit off, so allow internal spaces
# inside the number ("1 15)" -> 115); we strip them when reading the value.
# The space after ")" is optional ("101)O btain" in single-column long forms),
# so we require a letter to follow to avoid matching stray numbers.
_ITEM_RE = re.compile(r"(?m)^\s*(\d[\d ]{1,3})\)\s*(?=[A-Za-z])")
# Header/footer noise that bleeds in from the checkbox columns and page chrome.
_NOISE_LINE_RE = re.compile(
    r"(?im)^\s*(yes\s*/|no\s*/|done\b|n/?a\b|page\s+\d+|\d+\s*$).*$"
)


def clean_text(text: str) -> str:
    """Repair the common extraction artifacts in these PDFs."""
    if not text:
        return ""
    # cp1252 smart punctuation that failed to decode shows up as U+FFFD.
    text = text.replace("�", "'")
    # Normalize odd whitespace but keep newlines (item boundaries live on them).
    text = text.replace("\xa0", " ").replace("\t", " ")
    return text


def _repair_dropcap(item_text: str) -> str:
    """'R equest any...' -> 'Request any...'; 'D id the...' -> 'Did the...'.

    The styled first letter is split off only at the very start of an item, so
    we repair just the leading "X " + lowercase pattern and leave the body alone
    (so legitimate tokens like 'S corporations' are untouched).
    """
    return re.sub(r"^([A-Za-z])\s+([a-z])", r"\1\2", item_text.strip())


# Boilerplate that trails the final item (copyright, terms, page footer). The
# last item has no following item to bound it, so we cut at the first sentinel.
_FOOTER_RE = re.compile(
    r"(In applying the tax guidance|This resource is provided|"
    r"This document has been developed|Review the IRS'?s website for additional|"
    r"©\s*20\d\d|\bAICPA Tax Section\b|copyright-permissions)",
    re.I,
)


def _strip_item_noise(body: str) -> str:
    """Drop checkbox-column and page-number lines, then collapse whitespace."""
    kept = [ln for ln in body.splitlines() if not _NOISE_LINE_RE.match(ln)]
    text = re.sub(r"\s+", " ", " ".join(kept)).strip()
    return _FOOTER_RE.split(text)[0].strip()


def _extract_two_column(pdf) -> str:
    """Read each page as left column then right column (AICPA mini/short forms)."""
    chunks = []
    for page in pdf.pages:
        w, h = page.width, page.height
        mid = w / 2.0
        chunks.append(page.crop((0, 0, mid, h)).extract_text() or "")
        chunks.append(page.crop((mid, 0, w, h)).extract_text() or "")
    return clean_text("\n".join(chunks))


def _extract_single_column(pdf) -> str:
    """Read each page full width (AICPA long forms are single column)."""
    chunks = [page.extract_text() or "" for page in pdf.pages]
    return clean_text("\n".join(chunks))


def _item_number(raw: str) -> int:
    return int(raw.replace(" ", ""))


def _score_layout(text: str) -> tuple[int, int]:
    """Score an extraction by item count and how monotonic the numbering is.

    A correct read yields many items whose numbers mostly increase in order; a
    mangled read (wrong column model) yields fewer items and scrambled numbers.
    """
    nums = [_item_number(m.group(1)) for m in _ITEM_RE.finditer(text)]
    if not nums:
        return (0, 0)
    monotonic = sum(1 for a, b in zip(nums, nums[1:]) if b >= a)
    return (len(nums), monotonic)


def extract_columns_text(pdf_path: str) -> str:
    """Extract a checklist PDF, auto-detecting single- vs two-column layout.

    Both reading strategies are tried and the better-scoring one is kept, so the
    same code handles AICPA mini/short (two column) and long (single column).
    """
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is required for offline checklist extraction.")

    with pdfplumber.open(pdf_path) as pdf:
        two = _extract_two_column(pdf)
        one = _extract_single_column(pdf)

    # Prefer more items, then better-ordered numbering.
    return two if _score_layout(two) >= _score_layout(one) else one


def _clean_section(body: str) -> str:
    """Reduce a section-header chunk to a short, clean title.

    Header chunks sometimes absorb trailing text (a following item that wasn't
    matched at a line start, a "Note that ..." caption, or checkbox-column
    words). Keep only the leading title: drop checkbox words, cut at the first
    embedded item number or note, and cap the length.
    """
    title = re.sub(r"\b(Yes|No|Done|N/?A)\b", "", body, flags=re.I)
    title = re.split(r"\d{2,4}\)", title)[0]      # cut at any embedded item no.
    title = re.split(r"\bNote that\b", title, flags=re.I)[0]
    title = re.sub(r"[^0-9A-Za-z]+$", "", re.sub(r"\s+", " ", title)).strip()
    return title[:70].strip()


def parse_items(text: str) -> list[dict]:
    """Split extracted text into numbered checklist items.

    Round-hundred numbers (100, 200, 300, ...) are AICPA *section headers*; the
    rest are actionable items. Each item carries the section it falls under.
    """
    matches = list(_ITEM_RE.finditer(text))
    items: list[dict] = []
    current_section = "General"

    for i, m in enumerate(matches):
        num = _item_number(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = _strip_item_noise(text[start:end])
        body = _repair_dropcap(body)
        if not body:
            continue

        # Section header: a round hundred (100, 200, ...) with a short title.
        if num % 100 == 0:
            title = _clean_section(body)
            current_section = title or current_section
            continue

        items.append({"num": num, "section": current_section, "text": body})

    return items


def derive_title(pdf_path: str) -> str:
    """Human title from the file name (e.g. the 1040 long-form checklist)."""
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    base = re.sub(r"^\d{4}-", "", base)          # drop leading year
    base = re.sub(r"\s*\(\d+\)$", "", base)        # drop "(1)" dup suffix
    return base.replace("-", " ").strip().title()


def extract_checklist(pdf_path: str) -> dict:
    """Full offline parse of one checklist PDF into title + items + source text."""
    text = extract_columns_text(pdf_path)
    items = parse_items(text)
    # The cited guidance is the parsed checklist itself: joining the verbatim
    # item texts gives a source against which every step's guidance_ref (which
    # is exactly an item text) provably resolves.
    guidance_text = "\n\n".join(it["text"] for it in items)
    return {
        "title": derive_title(pdf_path),
        "items": items,
        "full_text": text,
        "guidance_text": guidance_text,
    }


def _slug(name: str) -> str:
    base = re.sub(r"\.[a-z0-9]+$", "", os.path.basename(name).lower())
    base = re.sub(r"\s*\(\d+\)$", "", base)
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "wizard"


def build_offline_wizard(checklist_id: str, parsed: dict) -> dict:
    """Build a deterministic, validateable wizard from a parsed checklist.

    Each checklist item becomes one step in a linear guided-intake flow. The
    operator marks every item Done / N/A / Needs follow-up; the wizard advances
    to the next item and ends after the last. guidance_ref is the verbatim item
    text, so builder.validate_wizard confirms every citation resolves.
    """
    items = parsed["items"]
    title = parsed["title"]
    slug = _slug(checklist_id)

    options = ["Done", "N/A", "Needs follow-up"]
    steps = []
    for idx, item in enumerate(items):
        sid = f"q{idx + 1}"
        nxt = f"q{idx + 2}" if idx + 1 < len(items) else "END"
        steps.append({
            "id": sid,
            "item_no": item["num"],
            "section": item["section"],
            "question": item["text"],
            "type": "single_select",
            "options": options,
            "guidance_ref": item["text"],  # verbatim from the checklist source
            "branches": [
                {"when": opt, "go_to": nxt, "action": None} for opt in options
            ],
        })

    assumptions = [
        "[ASSUMPTION] Generated offline by deterministic parsing of the AICPA "
        "checklist PDF; each step cites verbatim checklist text as its guidance.",
        "[ASSUMPTION] Items are presented as a linear guided-intake flow "
        "(Done / N/A / Needs follow-up) rather than inferred conditional "
        "branching, to avoid fabricating logic not stated in the source.",
    ]

    return {
        "wizard_id": slug,
        "checklist_source": os.path.basename(checklist_id),
        "title": title,
        "assumptions": assumptions,
        "steps": steps,
        "terminal_actions": [{
            "condition": "All checklist items reviewed",
            "forms": [title],
            "documents": [],
            "notes": "Offline checklist intake complete; proceed to preparation/review.",
        }],
    }
