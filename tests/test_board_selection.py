import pytest
from fastapi.testclient import TestClient
import config
from agent import board as boards
from agent.board import Board, ARM_DWELL_SECONDS


def part(card, **overrides):
    return dict(src=card.src,mesh=0,face=2,kind='component',name='Engine bell',**overrides)


def test_selection_context_clears_and_does_not_survive_geometry_change():
    b=Board(); c=b.add('model','Rocket',src='rocket.glb')
    detail=part(c)
    assert b.select(c.id,detail)['selected_part']==detail
    b._pointed=(c.id,100)
    assert b.pointed(101)['selected_part']==detail
    c.src='rocket-v2.glb'
    assert 'selected_part' not in b.selection()
    b.select(c.id)
    assert 'selected_part' not in b.selection()


@pytest.mark.parametrize('field,value',[('src','stale.glb'),('mesh',True),('face',-1),('kind','script'),('name',''),('name','x'*121)])
def test_invalid_detail_is_rejected_without_changing_selection(field,value):
    b=Board(); c=b.add('model','Rocket',src='rocket.glb'); b.select(c.id)
    detail=part(c);detail[field]=value
    with pytest.raises(ValueError): b.select(c.id,detail)
    assert 'selected_part' not in b.selection()


def test_tap_retains_original_hit_position():
    b=Board(); c=b.add('model','Rocket',src='rocket.glb',x=.5,y=.5)
    for t,x,p in [(0,.52,True),(ARM_DWELL_SECONDS+.01,.52,True),(ARM_DWELL_SECONDS+.10,.53,False)]:
        b.apply_hands([(x,.5,p,False,0)],now=t)
    tap=next(e for e in b.events_since(0) if e['type']=='tapped')
    assert tap['x']==.52 and tap['y']==.5


def test_endpoint_passes_region_to_conversation_and_checks_origin(monkeypatch):
    from dashboard.server import app
    from dashboard.companion import workspace_message
    b=Board(); c=b.add('model','Rocket',src='rocket.glb')
    monkeypatch.setattr(boards,'get_board',lambda:b)
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','selection-test')
    client=TestClient(app);headers={'Authorization':'Bearer selection-test'}
    payload={'id':c.id,'part':part(c),'workspace':b.workspace_context()}
    res=client.post('/api/board/select',json=payload,headers=headers)
    assert res.status_code==200,res.text
    assert res.json()['selection']['selected_part']['name']=='Engine bell'
    assert 'Engine bell' in workspace_message({'workspace':'board'},'Explain this')
    assert client.post('/api/board/select',json=payload,headers={**headers,'Origin':'https://evil.example'}).status_code==403


def test_renderer_hit_can_reach_large_model_edge_but_expires(monkeypatch):
    now=[100.0];monkeypatch.setattr(boards.time,'time',lambda:now[0])
    b=Board();c=b.add('model','Large rocket',src='rocket.glb',x=.5,y=.5)
    assert b._nearest(.9,.8,1) is None
    b.report_model_hits([dict(id=c.id,src=c.src,x=.9,y=.8,hand=1)])
    assert b._nearest(.9,.8,1) is c
    assert b._nearest(.9,.8,2) is None
    assert b._nearest(.8,.8,1) is None
    now[0]+=.36
    assert b._nearest(.9,.8,1) is None
    b.report_model_hits([dict(id=c.id,src=c.src,x=.9,y=.8,hand=1)])
    c.src='new.glb';assert b._nearest(.9,.8,1) is None


def test_bad_hits_do_not_replace_prior_valid_state():
    b=Board()
    with pytest.raises(ValueError): b.report_model_hits([dict(hand=0,x=float('nan'),y=.4)])
    assert not b._model_hits
