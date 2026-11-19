"""User-facing times use KST; persisted instants remain UTC epoch seconds."""
from datetime import datetime, timedelta, timezone

KST=timezone(timedelta(hours=9),"KST")
def kst(timestamp):
    if timestamp is None:return None
    return datetime.fromtimestamp(float(timestamp),KST).strftime("%Y-%m-%d %H:%M:%S KST (Asia/Seoul)")
