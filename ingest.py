import os
import json
import hashlib
import pandas as pd
from pathlib import Path
import logging

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

logger = logging.getLogger(__name__)

def compute_file_hash(filepath: str) -> str:
    """Computes the SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()

def extract_text_from_pdf(filepath: str) -> str:
    if pdfplumber is None:
        logger.warning(f"pdfplumber not installed, skipping PDF {filepath}")
        return f"[PDF parsing skipped for {filepath} - pdfplumber not installed]"
    
    try:
        text_content = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_content.append(text)
        return "\n".join(text_content)
    except Exception as e:
        logger.error(f"Error parsing PDF {filepath}: {e}")
        return f"[Error parsing PDF: {e}]"

def extract_text_from_csv(filepath: str) -> str:
    try:
        df = pd.read_csv(filepath)
        return df.to_csv(index=False)
    except Exception as e:
        logger.error(f"Error parsing CSV {filepath}: {e}")
        return f"[Error parsing CSV: {e}]"

def fetch_file_content(filepath: str) -> str:
    """Reads a file and returns its content as a string, handling different formats."""
    ext = os.path.splitext(filepath)[1].lower()
    
    if ext == '.pdf':
        return extract_text_from_pdf(filepath)
    elif ext in ['.csv', '.tsv']:
        if ext == '.tsv':
            try:
                df = pd.read_csv(filepath, sep='\t')
                return df.to_csv(index=False, sep='\t')
            except Exception as e:
                 logger.error(f"Error parsing TSV {filepath}: {e}")
                 return f"[Error parsing TSV: {e}]"
        return extract_text_from_csv(filepath)
    elif ext == '.json':
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return json.dumps(data, indent=2)
        except Exception as e:
            logger.error(f"Error parsing JSON {filepath}: {e}")
            return f"[Error parsing JSON: {e}]"
    else:
        # Default to plain text
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            logger.warning(f"File {filepath} is not UTF-8, attempting fallback.")
            try:
                with open(filepath, 'r', encoding='latin-1') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Failed to read text file {filepath}: {e}")
                return f"[Error reading file: {e}]"

def get_all_files(directory: str) -> list[str]:
    """Recursively gets all files from a directory."""
    file_list = []
    if not os.path.exists(directory):
        return file_list
        
    for root, _, files in os.walk(directory):
        for file in files:
            file_list.append(os.path.join(root, file))
    return file_list

def load_documents(directory: str) -> dict[str, dict]:
    """
    Loads all documents from a directory.
    Returns a dict mapping filename/id to its content and hash.
    """
    docs = {}
    files = get_all_files(directory)
    for filepath in files:
        doc_id = os.path.relpath(filepath, directory)
        content = fetch_file_content(filepath)
        file_hash = compute_file_hash(filepath)
        docs[doc_id] = {
            'filepath': filepath,
            'content': content,
            'hash': file_hash
        }
    return docs

def load_mapping(mapping_path: str) -> dict[str, str]:
    """Loads checklist to guidance mapping if it exists."""
    mapping = {}
    if os.path.exists(mapping_path):
        try:
            df = pd.read_csv(mapping_path)
            if 'checklist_id' in df.columns and 'guidance_ref' in df.columns:
                for _, row in df.iterrows():
                    mapping[str(row['checklist_id'])] = str(row['guidance_ref'])
        except Exception as e:
             logger.error(f"Error loading mapping file {mapping_path}: {e}")
    return mapping
