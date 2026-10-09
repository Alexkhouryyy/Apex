"""HGP confidence deferral over explicit scope metadata and frozen classifier scores.

This routes types, not permission. The operator must separately approve evidence.
No automatic labeling, training, model download or credential use occurs here.
"""
import math
from dataclasses import replace
from agent._vendor.hgp.threshold import Threshold

TYPES={'semantic':'fact','episodic':'episode','procedural':'procedure','none':None}


def threshold(iteration=0, *, initial=.9, floor=.5, beta=.05):
    if type(iteration) is not int or iteration<0 or any(type(v) not in (int,float) or not math.isfinite(v) for v in (initial,floor,beta)) or not 0<=floor<=initial<=1 or beta<0:
        raise ValueError('valid calibrated threshold schedule required')
    adapter=Threshold();adapter.state={'iterations':iteration};adapter.tau_0=initial;adapter.tau_min=floor;adapter.beta=beta
    return adapter.get_current_threshold()


def route(unit, probabilities, *, iteration=0, source):
    if not source or set(probabilities)!=set(TYPES) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in probabilities.values()):
        raise ValueError('frozen classifier scores need all typed labels and provenance')
    cut=threshold(iteration)
    # Multi-label scores are independent sigmoid outputs, not a distribution.
    above=[k for k,v in probabilities.items() if v>=cut]
    if len(above)!=1:return {'status':'defer','reason':'uncertain_or_multilabel','threshold':cut,'authorization':False}
    label=above[0]
    if label=='none':return {'status':'skip','threshold':cut,'authorization':False}
    try:candidate=replace(unit,kind=TYPES[label])
    except ValueError:return {'status':'defer','reason':'missing_type_support','threshold':cut,'authorization':False}
    return {'status':'routed','unit':candidate,'classifier_source':source,'threshold':cut,'authorization':False}
