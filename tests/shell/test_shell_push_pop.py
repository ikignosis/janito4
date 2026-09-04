from janito.shell.interactive import InteractiveShell


def _shell():
    sh = InteractiveShell.__new__(InteractiveShell)
    sh.model = "m"
    from janito.shell.stack import ConversationStack

    sh.conversation_stack = ConversationStack()
    sh.messages_history = [{"role": "system", "content": "s"}]
    sh.history_turns = []
    sh.previous_response_id = None
    sh.conversation_items = None
    sh.conversation_turn = 0
    sh.response_chain = []
    sh.response_turn = 0
    sh.mirrored_history = []
    sh.mirrored_turn = 0
    return sh


def test_push_pop_nested_isolation():
    sh = _shell()
    sh.messages_history.append({"role": "user", "content": "a"})
    sh.conversation_stack.push(sh)
    sh.messages_history.append({"role": "user", "content": "b"})
    assert sh.conversation_stack.depth == 1
    sh.conversation_stack.pop(sh)
    assert [m["content"] for m in sh.messages_history] == ["s", "a"]
    assert sh.conversation_stack.depth == 0


def test_pop_empty_raises():
    sh = _shell()
    try:
        sh.conversation_stack.pop(sh)
        assert False
    except IndexError:
        pass


def test_full_stack_clear():
    sh = _shell()
    sh._system_prompt = "s"
    sh.conversation_stack.push(sh)
    sh.initialize_history(system_prompt="s")
    assert sh.conversation_stack.depth == 0
    assert sh.messages_history == [{"role": "system", "content": "s"}]
