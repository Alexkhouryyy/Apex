from fastapi import FastAPI
from fastapi.testclient import TestClient
from dashboard.research import router


def client():
    app=FastAPI()
    @app.middleware('http')
    async def identify(request,call_next):
        request.state.is_master=request.headers.get('x-owner')=='yes'
        return await call_next(request)
    app.include_router(router)
    return TestClient(app)


def test_owner_scoped_memory_inspect_citations_forget_and_invalid_body(test_db):
    c=client();headers={'x-owner':'yes','Origin':'http://testserver'}
    assert c.get('/api/research/memory').status_code==403
    payload=dict(text='citation source fact',domain='work',purposes=['answer'],subject='other',approved=False)
    response=c.post('/api/research/memory',json=payload,headers=headers)
    assert response.status_code==200,response.text
    ident=response.json()['id']
    records=c.get('/api/research/memory',headers=headers).json()
    assert records[0]['subject']=='owner' and records[0]['approved']
    recalled=c.post('/api/research/memory/recall',json=dict(query='citation',domain='work',purpose='answer'),headers=headers)
    assert recalled.json()['memories'][0]['id']==ident
    assert c.post('/api/research/memory/recall',json={},headers=headers).status_code==400
    wrong_origin={**headers,'Origin':'https://evil.example'}
    assert c.post(f'/api/research/memory/{ident}/forget',headers=wrong_origin).status_code==403
    assert c.post(f'/api/research/memory/{ident}/forget',headers=headers).json()['deleted']==[ident]
    assert c.get('/api/research/memory',headers=headers).json()==[]


def test_archive_revive_owner_only_and_missing_archive_is_explicit(test_db):
    from agent.context_graph import ContextGraph,save_archive
    messages=[dict(role='user',content='preserved original instruction')]
    save_archive('fixture',messages);ident=ContextGraph.from_messages(messages).blocks[0].id
    c=client();headers={'x-owner':'yes','Origin':'http://testserver'}
    assert c.post('/api/research/context/fixture/revive',json={'ids':[ident]},headers=headers).json()==messages
    assert c.post('/api/research/context/fixture/revive',json={'ids':[ident]}).status_code==403
    assert c.post('/api/research/context/missing/revive',json={'ids':[ident]},headers=headers).status_code==400
