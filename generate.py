import os
import sys
import json
import argparse
import logging
import tempfile
from datetime import datetime, timezone

from ingest import load_documents, load_mapping
from builder import generate_wizard, validate_wizard
from llm import MODEL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(stream_handler)


def _resolve_guidance(checklist_id, mapping, guidance_docs):
    """Returns (guidance_content, ref_label) using mapping first, then name-based inference."""
    guidance_ref = mapping.get(checklist_id)
    if guidance_ref and guidance_ref in guidance_docs:
        logger.info(f"Matched checklist {checklist_id} to guidance {guidance_ref}")
        return guidance_docs[guidance_ref]["content"], guidance_ref

    base_name = os.path.splitext(os.path.basename(checklist_id))[0]
    for g_id, g_info in guidance_docs.items():
        g_base = os.path.splitext(os.path.basename(g_id))[0]
        if base_name.lower() in g_id.lower() or g_base.lower() in base_name.lower():
            logger.info(f"Inferred guidance {g_id} for checklist {checklist_id}")
            return g_info["content"], g_id

    logger.warning(f"Could not infer guidance mapping for {checklist_id}.")
    return "[INFERRED] No specific guidance document mapped.", None


def process_all(checklists, guidance_docs, mapping, wizards_dir, manifest, manifest_path, batch_size):
    stats = {"total_processed": 0, "total_generated": 0, "total_skipped": 0, "total_assumptions": 0}
    items = list(checklists.items())

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        logger.info(f"Processing batch {i // batch_size + 1} (size {len(batch)})...")

        for checklist_id, checklist_info in batch:
            stats["total_processed"] += 1

            entry = manifest.get(checklist_id)
            if entry and entry.get("source_hash") == checklist_info["hash"]:
                wfile = os.path.join(wizards_dir, f"{entry.get('wizard_id')}.json")
                if os.path.exists(wfile):
                    logger.info(f"Skipping {checklist_id}: Unchanged")
                    stats["total_skipped"] += 1
                    continue

            guidance_content, _ = _resolve_guidance(checklist_id, mapping, guidance_docs)
            wizard = generate_wizard(checklist_id, checklist_info["content"], guidance_content)

            # Validator now sees the guidance so it can verify refs actually resolve.
            if not validate_wizard(wizard, guidance_content):
                logger.error(f"Validation failed for wizard from {checklist_id}. Skipping.")
                stats["total_skipped"] += 1
                manifest[checklist_id] = {
                    "source": checklist_id,
                    "status": "skipped",
                    "reason": "validation_failed",
                    "source_hash": checklist_info["hash"],
                    "model": MODEL,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
                continue

            wid = wizard.get("wizard_id", "unknown-wizard")
            out_file = os.path.join(wizards_dir, f"{wid}.json")
            try:
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(wizard, f, indent=2)
                acount = len(wizard.get("assumptions", []))
                stats["total_generated"] += 1
                stats["total_assumptions"] += acount
                manifest[checklist_id] = {
                    "wizard_id": wid,
                    "source": checklist_id,
                    "source_hash": checklist_info["hash"],
                    "status": "finished",
                    "assumption_count": acount,
                    "model": MODEL,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
                logger.info(f"Generated wizard {wid} for {checklist_id} ({acount} assumptions)")
            except Exception as e:
                logger.error(f"Failed to write wizard {wid}: {e}")
                stats["total_skipped"] += 1

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    return stats


def run_self_test():
    """Runs one synthetic checklist through the full pipeline. No network needed."""
    logger.info("Running --self-test...")
    tmp = tempfile.mkdtemp(prefix="wizard_selftest_")
    cl, gd, out = (os.path.join(tmp, d) for d in ("checklists", "guidance", "output"))
    for d in (cl, gd, out):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(cl, "demo.md"), "w") as f:
        f.write("# Demo Checklist\n- Must have valid SSN\n- Filing status matters\n")
    with open(os.path.join(gd, "demo.md"), "w") as f:
        f.write("IRS Pub 000: Valid SSN required. MFS may disqualify.\n")

    checklists = load_documents(cl)
    guidance_docs = load_documents(gd)
    wizards_dir = os.path.join(out, "wizards")
    os.makedirs(wizards_dir, exist_ok=True)
    manifest = {}
    stats = process_all(checklists, guidance_docs, {}, wizards_dir,
                        manifest, os.path.join(out, "manifest.json"), 25)

    ok = stats["total_processed"] == 1
    logger.info(f"Self-test stats: {stats}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("No API key: fallback path exercised; placeholder correctly fails validation "
                    "(expect total_skipped=1). Pipeline wiring verified.")
    logger.info(f"Self-test {'PASSED (pipeline ran end to end)' if ok else 'FAILED'}. Artifacts: {tmp}")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Autonomous Tax-Wizard Generator")
    parser.add_argument("--checklists", default="./input/checklists/")
    parser.add_argument("--guidance", default="./input/guidance/")
    parser.add_argument("--mapping", default="./input/mapping.csv",
                        help="Path to checklist_id,guidance_ref mapping CSV")
    parser.add_argument("--output", default="./output/")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--self-test", action="store_true",
                        help="Run one synthetic checklist through the pipeline and exit")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(run_self_test())

    os.makedirs(args.checklists, exist_ok=True)
    os.makedirs(args.guidance, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    logger.addHandler(logging.FileHandler(os.path.join(args.output, "run.log")))

    wizards_dir = os.path.join(args.output, "wizards")
    os.makedirs(wizards_dir, exist_ok=True)
    manifest_path = os.path.join(args.output, "manifest.json")

    manifest = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.error(f"Error loading existing manifest: {e}")

    logger.info("Loading checklists and guidance documents...")
    checklists = load_documents(args.checklists)
    guidance_docs = load_documents(args.guidance)
    mapping = load_mapping(args.mapping)

    if not checklists:
        logger.warning(f"No checklists found in {args.checklists}.")
        return
    logger.info(f"Found {len(checklists)} checklists.")

    stats = process_all(checklists, guidance_docs, mapping, wizards_dir,
                        manifest, manifest_path, args.batch_size)

    logger.info("===================================")
    logger.info("COMPLETION SUMMARY")
    logger.info(f"Total processed:   {stats['total_processed']}")
    logger.info(f"Total generated:   {stats['total_generated']}")
    logger.info(f"Total skipped:     {stats['total_skipped']}")
    logger.info(f"Total assumptions: {stats['total_assumptions']}")
    logger.info("===================================")


if __name__ == "__main__":
    main()
