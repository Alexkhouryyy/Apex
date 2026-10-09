"""Narrow exact-quote verification against operator-supplied source snapshots.

This proves literal quote occurrence and source identity, not semantic entailment,
source credibility, or current facts. Missing sources and empty quotes abstain.
"""
import hashlib


def verify(quote, source_id, sources):
    if not isinstance(quote,str) or not quote.strip() or not isinstance(source_id,str):
        return {'verdict':'abstain','reason':'invalid_input'}
    if source_id not in sources:return {'verdict':'abstain','reason':'source_unavailable'}
    text=sources[source_id]
    if not isinstance(text,str):raise ValueError('sources must be frozen text snapshots')
    present=quote in text
    return {'verdict':'pass' if present else 'fail','reason':'literal_match' if present else 'quote_not_found',
            'source_sha':hashlib.sha256(text.encode()).hexdigest(),'source_id':source_id,
            'position':text.find(quote) if present else None,'authorization':False}
