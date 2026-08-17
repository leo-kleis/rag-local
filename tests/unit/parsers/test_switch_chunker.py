from rag_local.parsers.python import chunk_python
from rag_local.parsers.typescript.ast import extract_event_and_action_tags
from rag_local.parsers.typescript.chunker import chunk_typescript


def test_extract_event_and_action_tags():
    code = """
    socket.emit('user_joined', { userId: 123 });
    socket.on('message_received', (data) => console.log(data));
    @SubscribeMessage('room_created')
    handleRoom() {}
    dispatch({ type: 'USER_LOGOUT' });
    """
    tags = extract_event_and_action_tags(code)
    assert "event:user_joined" in tags
    assert "event:message_received" in tags
    assert "event:room_created" in tags
    assert "action:USER_LOGOUT" in tags


def test_small_reducer_single_chunk():
    code = """import { UserState } from './types';

export function smallReducer(state: UserState, action: any) {
    switch (action.type) {
        case 'set_name':
            return { ...state, name: action.payload };
        case 'set_age':
            return { ...state, age: action.payload };
        default:
            return state;
    }
}
"""
    lines = code.splitlines(keepends=True)
    chunks = chunk_typescript(lines)

    # Reducer pequeño no debe fragmentarse por casos (1 imports + 1 función)
    assert len(chunks) == 2
    assert chunks[1].metadata.class_name == "smallReducer"
    assert chunks[1].metadata.type == "function"


def test_large_reducer_case_chunking():
    cases = []
    for i in range(12):
        cases.append(f"""        case 'action_event_{i}':
            // Logic for action {i}
            const updatedState{i} = {{ ...state, count: state.count + {i} }};
            console.log('Action {i} processed successfully with state update');
            return updatedState{i};
""")

    code = f"""import {{ AppState }} from './state';
import {{ logAction }} from './logger';

export function appReducer(state: AppState, action: any) {{
    switch (action.type) {{
{"".join(cases)}
        default:
            return state;
    }}
}}
"""
    lines = code.splitlines(keepends=True)
    assert len(lines) > 60

    chunks = chunk_typescript(lines)

    # Debe contener 1 chunk de imports + 13 chunks de casos (12 cases + 1 default)
    assert len(chunks) >= 13

    # Comprobar el primer caso
    case_chunk = next(c for c in chunks if "action:action_event_0" in c.metadata.tags)
    assert case_chunk.metadata.class_name == "appReducer"
    assert case_chunk.metadata.method_name == "appReducer:action_event_0"
    assert case_chunk.metadata.type == "reducer_case"
    assert "import { AppState } from './state';" in case_chunk.text
    assert "export function appReducer" in case_chunk.text
    assert "case 'action_event_0':" in case_chunk.text

    # Comprobar el default case
    default_chunk = next(
        c for c in chunks if c.metadata.method_name == "appReducer:default"
    )
    assert "default:" in default_chunk.text


def test_const_arrow_reducer_chunking():
    cases = []
    for i in range(16):
        cases.append(f"""        case 'update_prop_{i}':
            // Update logic for property {i}
            const nextVal{i} = action.payload * {i};
            console.log('Processed property update', nextVal{i});
            return {{ ...state, val{i}: nextVal{i} }};
""")

    code = f"""import {{ State }} from './types';

export const chatStateReducer = (state: State, action: any) => {{
    switch (action.type) {{
{"".join(cases)}
        default:
            return state;
    }}
}};
"""
    lines = code.splitlines(keepends=True)
    assert len(lines) > 60

    chunks = chunk_typescript(lines)

    # Debe reconocer chatStateReducer como función nombrada y segmentar los casos
    assert len(chunks) >= 17
    case_chunk = next(c for c in chunks if "action:update_prop_1" in c.metadata.tags)
    assert case_chunk.metadata.class_name == "chatStateReducer"
    assert case_chunk.metadata.method_name == "chatStateReducer:update_prop_1"
    assert "chatStateReducer" in case_chunk.text
    assert "case 'update_prop_1':" in case_chunk.text


def test_python_event_tags_extraction():
    code = """import socketio

sio = socketio.Server()

@sio.event
def connect(sid, environ):
    sio.emit('welcome_user', {'sid': sid})

@sio.on('chat_send')
def handle_chat(sid, data):
    socketio.emit('broadcast_msg', data)
"""
    lines = code.splitlines(keepends=True)
    chunks = chunk_python(lines)

    all_tags = set()
    for c in chunks:
        if isinstance(c.metadata.tags, list):
            all_tags.update(c.metadata.tags)

    assert "event:welcome_user" in all_tags
    assert "event:broadcast_msg" in all_tags


def test_iife_wrapped_reducer_chunking():
    cases = []
    for i in range(16):
        cases.append(f"""            case 'iife_action_{i}':
                console.log('Action {i} in IIFE');
                state.val = {i};
                return state;
""")

    code = f"""(function() {{
    const state = {{ val: 0 }};
    function iifeReducer(state, action) {{
        switch (action.type) {{
{"".join(cases)}
            default:
                return state;
        }}
    }}
    window.dispatch = (action) => iifeReducer(state, action);
}})();
"""
    lines = code.splitlines(keepends=True)
    assert len(lines) > 60

    chunks = chunk_typescript(lines)

    # Debe segmentar los casos dentro de la IIFE
    assert len(chunks) >= 16
    case_chunk = next(c for c in chunks if "action:iife_action_2" in c.metadata.tags)
    assert "iifeReducer:iife_action_2" in case_chunk.metadata.method_name
    assert "case 'iife_action_2':" in case_chunk.text

