import numpy as np
from network.reliability_v24 import *

def test_theoretical_availability(): assert abs(theoretical_availability(90,10)-.9)<1e-12
def test_validation():
    try: ReliabilitySpec('x',0,2).validate(); assert False
    except ValueError: pass
def test_seed_reproducible():
    s=ReliabilityStudy(.2,1,20,42,[ReliabilitySpec('A',20,3)])
    assert run_reliability(s,100)['realizations']==run_reliability(s,100)['realizations']
def test_planned_outage_reduces_availability():
    a=run_reliability(ReliabilityStudy(.1,1,1,1,[ReliabilitySpec('A',1e12,1,((0,10),))]),100)
    assert a['summary']['mean_availability'] < 0.8
def test_redundancy_improves_availability():
    one=run_reliability(ReliabilityStudy(1,1,200,4,[ReliabilitySpec('A',20,5)]),100)['summary']['mean_availability']
    two=run_reliability(ReliabilityStudy(1,1,200,4,[ReliabilitySpec('A',20,5,(), 'G',1),ReliabilitySpec('B',20,5,(), 'G',1)]),100)['summary']['mean_availability']
    assert two > one
def test_percentile_orientation():
    x=run_reliability(ReliabilityStudy(.2,1,30,7,[ReliabilitySpec('A',20,4)]),100)['summary']
    assert x['p90_availability'] <= x['p50_availability'] <= x['p10_availability']
def test_deferred_nonnegative():
    x=run_reliability(ReliabilityStudy(.2,1,5,7,[ReliabilitySpec('A',20,4)]),100)['summary']; assert x['mean_deferred_m3']>=0
