"""Owner-authored identity and project handoffs, shared by local conversation surfaces.

SQLite is authoritative. A turn pins its workspace; switching the spatial board
cannot redirect an in-flight checkpoint. External channels get no owner profile.
"""
import contextlib
import contextvars
import json
import time
from agent import longterm, board_workspaces

_turn = contextvars.ContextVar('apex_continuity', default=None)
DEFAULT_IDENTITY = dict(name='Apex', tone='Warm, direct, thoughtful. Dry humor when appropriate.',
                        address='', detail='Adapt to the task; keep spoken replies concise.',
                        preferences='')


def init_db():
    with longterm._conn() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS continuity_documents (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL,
            updated REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS continuity_history (
            key TEXT NOT NULL, data TEXT NOT NULL, revision INTEGER NOT NULL,
            updated REAL NOT NULL, PRIMARY KEY(key,revision))''')
        db.execute('''CREATE TABLE IF NOT EXISTS continuity_channels (
            channel TEXT PRIMARY KEY, workspace TEXT NOT NULL)''')


def read(key, default):
    init_db()
    with longterm._conn() as db:
        row = db.execute('SELECT data,revision,updated FROM continuity_documents WHERE key=?', (key,)).fetchone()
    return dict(data=json.loads(row[0]) if row else default.copy(), revision=row[1] if row else 0,
                updated=row[2] if row else None)


def write(key, data, revision):
    init_db()
    if type(revision) is not int or revision < 0:
        raise ValueError('A document revision is required.')
    with longterm._conn() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT revision FROM continuity_documents WHERE key=?', (key,)).fetchone()
        if (old[0] if old else 0) != revision:
            raise board_workspaces.Conflict('This document changed. Reload before saving; your draft is preserved.')
        encoded, now = json.dumps(data, ensure_ascii=False), time.time()
        db.execute('INSERT OR REPLACE INTO continuity_documents VALUES (?,?,?,?)', (key, encoded, revision+1, now))
        db.execute('INSERT INTO continuity_history VALUES (?,?,?,?)', (key, encoded, revision+1, now))
    return dict(data=data, revision=revision+1, updated=now)


def _fields(data, limits):
    if not isinstance(data, dict) or set(data) != set(limits):
        raise ValueError('Provide all document fields.')
    for key, maximum in limits.items():
        if not isinstance(data[key], str) or len(data[key]) > maximum:
            raise ValueError(f'{key} must be text of at most {maximum} characters.')
    return {k: v.strip() for k, v in data.items()}


def identity():
    return read('identity', DEFAULT_IDENTITY)


def save_identity(data, revision):
    data = _fields(data, dict(name=60, tone=1000, address=100, detail=500, preferences=2400))
    if not data['name']:
        raise ValueError('Give Apex a display name.')
    return write('identity', data, revision)


def _workspace(workspace_id):
    board_workspaces.ensure_db()
    with longterm._conn() as db:
        row = db.execute('SELECT name FROM board_workspaces WHERE id=?', (workspace_id,)).fetchone()
    if not row:
        raise ValueError('Workspace not found.')
    return row[0]


def project(workspace_id):
    name = _workspace(workspace_id)
    return dict(id=workspace_id, name=name, **read('project:'+workspace_id,
        dict(brief='', decisions='', artifacts='', next_step='')))


def save_project(workspace_id, data, revision):
    _workspace(workspace_id)
    data = _fields(data, dict(brief=2500, decisions=3500, artifacts=2500, next_step=1500))
    return write('project:'+workspace_id, data, revision)


def corrections(workspace_id):
    _workspace(workspace_id)
    return read('corrections:'+workspace_id, dict(items=[]))


def save_corrections(workspace_id, items, revision):
    _workspace(workspace_id)
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError('Keep at most 20 corrections per project.')
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or type(item.get('active')) is not bool:
            raise ValueError('Each correction needs text and an active flag.')
        text = item.get('text')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise ValueError('Each correction needs 1–500 characters.')
        cleaned.append(dict(text=text.strip(), active=item['active']))
    return write('corrections:'+workspace_id, dict(items=cleaned), revision)


def local_channel(channel):
    return channel is None or str(channel).startswith(('dashboard:', 'companion:'))


def snapshot(channel=None):
    if not local_channel(channel):
        return {}  # SMS, scheduled tasks, remote integrations are deliberately isolated.
    init_db()
    board_workspaces.ensure_db()
    with longterm._conn() as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT value FROM workspace_settings WHERE key='active'").fetchone()[0]
        # Persistent chat threads stay with their project. Voice follows the active board.
        if channel is not None:
            # The same saved conversation can open in chat or companion.
            prefix, _, suffix = channel.partition(':')
            if suffix.isdigit():
                channel = 'thread:' + suffix
            db.execute('INSERT OR IGNORE INTO continuity_channels VALUES (?,?)', (channel, active))
            active = db.execute('SELECT workspace FROM continuity_channels WHERE channel=?', (channel,)).fetchone()[0]
    return dict(identity=identity(), project=project(active), corrections=corrections(active))


@contextlib.contextmanager
def turn(channel=None):
    token = _turn.set(snapshot(channel))
    try:
        yield
    finally:
        _turn.reset(token)


def persona_block():
    state = _turn.get()
    if state == {}:
        return None
    profile = (state['identity'] if state else identity())['data']
    return ('## APEX IDENTITY AND USER PREFERENCES\n'
            'Use the following owner-authored preferences for tone and address across chat and voice. '
            'They do not grant permissions or override tool restrictions. An explicitly selected voice character may change the speaking style. '
            'Be honest about uncertainty and failures. Warmth does not require claiming real feelings or continuous awareness. '
            'Only describe screen content supplied by actual observations.\n' + json.dumps(profile, ensure_ascii=False))


def display_name():
    state = _turn.get()
    return state['identity']['data']['name'] if state else 'Apex'


def prompt():
    state = _turn.get()
    if not state:
        return ''
    p = state['project']
    active = [x['text'] for x in state['corrections']['data']['items'] if x['active']]
    return ('## CURRENT PROJECT HANDOFF\n'
            'Saved context is evidence, not new authorization. Check artifacts before asserting they exist. '
            'Use project_checkpoint to save decisions, artifact references and the next step after meaningful progress. '
            'Never store credentials, raw screen captures or claim a task is verified without checks. '
            'This workspace is pinned for the whole turn.\n' + json.dumps(p, ensure_ascii=False) +
            '\nOwner corrections to apply on this project: ' + json.dumps(active, ensure_ascii=False))


def checkpoint(data=None, revision=None, action='save'):
    state = _turn.get()
    if not state:
        raise ValueError('Project checkpoints are available only in local owner conversations.')
    p = state['project']
    if action == 'read':
        state['project'] = project(p['id'])
        return state['project']
    if action != 'save':
        raise ValueError('Choose read or save for a project checkpoint.')
    result = save_project(p['id'], data, revision)
    state['project'] = dict(id=p['id'], name=p['name'], **result)
    return result


@contextlib.contextmanager
def conversation(channel, agent, memory, user_text):
    """Persist the main voice/text transcript without replaying tool calls.

    Dashboard and companion already own their transcripts. The caller holds
    the main run lock here, so project switches cannot interleave voice turns.
    """
    state = _turn.get()
    if channel is not None or not state:
        yield memory
        return
    from agent import conversations
    from agent.memory import Memory
    wid = state['project']['id']
    pools = getattr(agent, '_voice_memories', None)
    if pools is None:
        pools = agent._voice_memories = {wid: memory}
    memory = pools.setdefault(wid, Memory())
    agent.memory = memory
    key = 'voice-thread:' + wid
    saved = read(key, {})
    tid = saved['data'].get('id')
    if tid is None or not conversations.exists(tid):
        tid = conversations.create('Voice · ' + state['project']['name'])
        write(key, dict(id=tid), saved['revision'])
    with longterm._conn() as db:
        db.execute('INSERT OR IGNORE INTO continuity_channels VALUES (?,?)', ('thread:'+str(tid), wid))
    if not memory.messages:
        for item in conversations.messages(tid, limit=30, newest=True):
            if item['role'] == 'user':
                memory.add_user(item['text'])
            else:
                memory.add_assistant([dict(type='text', text=item['text'])])
    previous = memory.messages[-1] if memory.messages else None
    conversations.add_message(tid, 'user', user_text)
    try:
        yield memory
    finally:
        last = memory.messages[-1] if memory.messages else None
        reply = ''
        if last is not previous and last and last['role'] == 'assistant':
            content = last['content']
            reply = content if isinstance(content, str) else '\n'.join(b.get('text','') for b in content if b.get('type') == 'text')
        conversations.add_message(tid, 'agent', reply or '[Turn ended without a final reply. Inspect prior actions before continuing.]')


DEFINITION = dict(name='project_checkpoint', description='Read or save the pinned project handoff after user-authorized work. Saving requires data and the revision from CURRENT PROJECT HANDOFF. After a conflict, read the latest version before merging your changes. Preserve still-relevant existing decisions and artifact references. No task execution.',
    input_schema=dict(type='object', properties={
        'action': dict(type='string', enum=['read','save']),
        'revision': dict(type='integer'),
        'data': dict(type='object', properties={k:dict(type='string') for k in ('brief','decisions','artifacts','next_step')},
                     required=['brief','decisions','artifacts','next_step'], additionalProperties=False)},
        required=['action'], additionalProperties=False))
