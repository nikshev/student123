// verifies: FR-002-08

const { ChatStateReducer } = require('../../ai_tutor_xblock/static/js/ai_tutor.js');

describe('ChatStateReducer blocked state (FR-002-08)', () => {
  const loadingState = (overrides = {}) => ({
    ui: 'LOADING',
    pending: false,
    request_id: null,
    saved_input: '',
    message: null,
    sources: [],
    ...overrides,
  });

  test('LOADING(pending:true) + ASK_BLOCKED({message:"rule"}) -> ui=="BLOCKED", sources deep-equal [], message=="rule"', () => {
    const state = ChatStateReducer.reduce(
      loadingState({ pending: true }),
      { type: 'ASK_BLOCKED', payload: { message: 'rule' } },
    );

    expect(state.ui).toBe('BLOCKED');
    expect(state.sources).toEqual([]);
    expect(state.message).toBe('rule');
  });

  test('BLOCKED + RESET -> READY', () => {
    const state = ChatStateReducer.reduce(
      loadingState({ ui: 'BLOCKED', message: 'rule' }),
      { type: 'RESET' },
    );

    expect(state.ui).toBe('READY');
  });

  test('SUBMIT із новим request_id -> LOADING, pending true, request_id==новий', () => {
    const newRequestId = '550e8400-e29b-41d4-a716-446655440001';
    const state = ChatStateReducer.reduce(
      loadingState({ ui: 'READY' }),
      {
        type: 'SUBMIT',
        payload: { request_id: newRequestId, input: 'нова питання' },
      },
    );

    expect(state.ui).toBe('LOADING');
    expect(state.pending).toBe(true);
    expect(state.request_id).toBe(newRequestId);
  });

  test('sources на BLOCKED порожні', () => {
    const state = ChatStateReducer.reduce(
      loadingState({ pending: true }),
      { type: 'ASK_BLOCKED', payload: { message: 'rule', sources: [{ x: 'y' }] } },
    );

    expect(state.sources).toEqual([]);
  });
});
