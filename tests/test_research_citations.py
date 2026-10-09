from agent.citation_checks import verify


def test_literal_quote_requires_source_and_does_not_authorize_or_infer_entailment():
    sources={'source':'The device uses 3.3 V.'}
    assert verify('uses 3.3 V.','source',sources)['verdict']=='pass'
    assert verify('uses 5 V.','source',sources)['verdict']=='fail'
    assert verify('uses 3.3 V.','missing',sources)['verdict']=='abstain'
    assert verify('','source',sources)['verdict']=='abstain'
    assert not verify('uses 3.3 V.','source',sources)['authorization']
