import pytest
from agent.context_graph import Block, ContextGraph, save_archive, load_archive
from agent.memory import Memory


def test_pairs_survive_or_are_omitted_together_and_revive_original():
    messages=[{'role':'assistant','content':[{'type':'tool_use','id':'call1','name':'read_file','input':{'path':'x'}}]}, {'role':'user','content':[{'type':'tool_result','tool_use_id':'call1','content':'result'}]}, {'role':'assistant','content':'irrelevant '*500}, {'role':'user','content':'current instruction'}]
    graph=ContextGraph.from_messages(messages,keep=1)
    result=graph.select('result',600)
    selected=set(result['selected_ids'])
    assert (graph.blocks[0].id in selected)==(graph.blocks[1].id in selected)
    assert result['messages'][-1]==messages[-1]
    assert graph.revive([graph.blocks[1].id])==messages[:2]
    result['messages'][-1]['content']='mutated'
    assert graph.revive([graph.blocks[-1].id])[0]['content']=='current instruction'


def test_protected_overflow_cannot_silently_drop_user_or_latest_write():
    messages=[{'role':'user','content':'instruction '*100}, {'role':'assistant','content':[{'type':'tool_use','id':'write','name':'write_file','input':{'path':'x'}}]}, {'role':'user','content':[{'type':'tool_result','tool_use_id':'write','content':'done'}]}]
    result=ContextGraph.from_messages(messages,keep=1).select('',10)
    assert result['status']=='protected_overflow' and result['messages']==messages
    with pytest.raises(ValueError):ContextGraph.from_messages([messages[-1]])


def test_persistent_canonical_history_keeps_previously_omitted_blocks(test_db,monkeypatch):
    import config
    monkeypatch.setattr(config,'CONTEXT_SELECTION_BYTES',2200)
    memory=Memory()
    memory.messages=[{'role':'assistant','content':f'raw{i} '+('x'*100)} for i in range(40)]+[{'role':'user','content':'question'}]
    memory._select_context()
    omitted=memory.context_selection['omitted_ids']
    assert omitted
    assert len(load_archive(memory.archive_id))==41
    memory.add_assistant([{'type':'text','text':'answer'}]);memory.add_user('next')
    memory._select_context()
    assert len(memory.raw_history())==43
    assert memory.revive([omitted[0]])[0]['content'].startswith('raw')
    restored=Memory();restored.restore_archive(memory.archive_id)
    assert restored.messages==memory.raw_history()


def test_missing_dependencies_fail_instead_of_partial_context():
    with pytest.raises(ValueError):ContextGraph([Block('x',{},('missing',))])


def test_sdk_assistant_blocks_are_canonical_request_dictionaries():
    from anthropic.types import TextBlock,ToolUseBlock
    memory=Memory();memory.add_assistant([TextBlock(type='text',text='answer'),ToolUseBlock(type='tool_use',id='call',name='read_file',input={'path':'x'})])
    assert memory.messages[0]['content'][0]['text']=='answer'
    assert isinstance(memory.messages[0]['content'][1],dict)
    assert memory.messages[0]['content'][1]['input']=={'path':'x'}


def test_archives_reject_rewriting_old_evidence(test_db):
    messages=[{'role':'user','content':'original'}]
    save_archive('append-only',messages)
    with pytest.raises(ValueError):save_archive('append-only',[{'role':'user','content':'changed'}])
    save_archive('append-only',messages+[{'role':'assistant','content':'new'}])
    assert load_archive('append-only')[0]['content']=='original'


def test_append_dependency_chain_and_opaque_shell_effects_stay_protected():
    messages=[]
    for i,name in enumerate(('write_file','append_file','append_file','bash')):
        messages += [{'role':'assistant','content':[{'type':'tool_use','id':str(i),'name':name,'input':{'path':'x'}}]}, {'role':'user','content':[{'type':'tool_result','tool_use_id':str(i),'content':'done'}]}]
    result=ContextGraph.from_messages(messages,keep=1).select('',1)
    assert result['status']=='protected_overflow' and result['messages']==messages
    with pytest.raises(ValueError):ContextGraph.from_messages(messages+messages[:2])
