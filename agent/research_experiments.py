"""Offline experiment readiness; incomplete or synthetic evidence cannot imply promotion."""
import math
import operator
from agent.learning_eval import digest

OPERATORS={'>=':operator.ge,'<=':operator.le,'==':operator.eq,'<':operator.lt,'>':operator.gt}


def assess(contract, evidence):
    reasons=[]
    if evidence.get('kind')!=contract['evidence_kind']:reasons.append('wrong_evidence_kind')
    if not evidence.get('source'):reasons.append('missing_independent_source')
    metrics=evidence.get('metrics',{})
    for name,(op,threshold) in contract['checks'].items():
        if op not in OPERATORS or type(threshold) not in (int,float) or not math.isfinite(threshold):
            raise ValueError('invalid fixed experiment check')
        value=metrics.get(name)
        if type(value) not in (int,float) or not math.isfinite(value):reasons.append('missing_or_invalid:'+name)
        elif not OPERATORS[op](value,threshold):reasons.append('failed:'+name)
    alternatives = contract.get('any_checks', {})
    if alternatives:
        satisfied = False
        for name, (op, threshold) in alternatives.items():
            if op not in OPERATORS or type(threshold) not in (int, float) or not math.isfinite(threshold):
                raise ValueError('invalid alternative experiment check')
            value = metrics.get(name)
            satisfied |= type(value) in (int, float) and math.isfinite(value) and OPERATORS[op](value, threshold)
        if not satisfied:
            reasons.append('no_alternative_target_met')
    return {'experiment':contract['id'],'status':'target_met' if not reasons else 'not_ready',
            'reasons':reasons,'contract_sha':digest(contract),'evidence_sha':digest(evidence),'authorization':False}
