# providers/gdrive.py
"""Google Drive 공개 폴더 유틸리티."""
import re


def extract_folder_id(value: str) -> str:
    """URL 또는 폴더 ID에서 순수 폴더 ID를 추출."""
    value = value.strip()
    m = re.search(r"folders/([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    return value
