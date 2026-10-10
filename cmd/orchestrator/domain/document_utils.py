"""Shared document/data helpers; no retired Zhongshu execution."""
import hashlib
def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def group_chapter(markdown: str, group_id: str) -> str:
    """Extract exact approved chapter; legacy single-group documents pass through."""
    if '<!-- requirement-group:' not in markdown:
        return markdown
    begin = f'<!-- requirement-group:{group_id}:begin -->\n'
    end = f'\n<!-- requirement-group:{group_id}:end -->'
    if markdown.count(begin) != 1 or markdown.count(end) != 1:
        raise ValueError('PLAN_REVIEW_GROUP_CHAPTER_MISSING:' + group_id)
    return markdown.split(begin, 1)[1].split(end, 1)[0]
