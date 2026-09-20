// verifies: FR-002-01

/**
 * ChatStateReducer contract tests (FR-002-01, T-027).
 *
 * AI Tutor student-view state machine: pure reducer with 7 UI states,
 * duplicate-submit guard, retry with same request_id + saved input,
 * synchronous UI (no navigation/streaming), accessible announcements,
 * safe source rendering.
 *
 * EXPECTED RED REASON (before T-028): module
 * '../../ai_tutor_xblock/static/js/ai_tutor.js' does not exist yet — jest
 * fails with "Cannot find module" and every test in this suite errors.
 * T-028 creates the module exporting ChatStateReducer with
 * createInitialState() and reduce(state, event).
 *
 * === ChatStateReducer CONTRACT (for T-028) ===
 *
 * Exports:
 *   ChatStateReducer.createInitialState() -> state
 *   ChatStateReducer.reduce(state, event) -> state
 *
 * state shape:
 *   {
 *     ui: 'LOADING'|'READY'|'NO_MATERIALS'|'OFF_TOPIC'|'BLOCKED'|
 *         'LIMIT_REACHED'|'ERROR',
 *     pending: boolean,
 *     request_id: string|null,   // UUID, preserved across retry
 *     saved_input: string,       // preserved across retry
 *     message: string|null,      // accessible announcement for screen readers
 *     sources: Array<{segment_id, kind, source_ref, excerpt}>,
 *   }
 *
 * Transition map (other event/state combos are ignored: same state returned):
 *   LOADING + MATERIALS_READY   -> READY (announcement set)
 *   LOADING + ASK_SUCCESS       -> READY (announcement set, sources from payload)
 *   LOADING + ASK_BLOCKED       -> BLOCKED (message = safe rule from payload)
 *   LOADING + ASK_NO_MATERIALS  -> NO_MATERIALS
 *   LOADING + ASK_OFF_TOPIC     -> OFF_TOPIC
 *   LOADING + ASK_LIMIT_REACHED -> LIMIT_REACHED
 *   LOADING + ASK_ERROR         -> ERROR
 *   READY   + SUBMIT            -> LOADING, pending=true, request_id and
 *                                 saved_input from payload
 *   LOADING + SUBMIT            -> ignored while pending=true (duplicate guard)
 *   ERROR   + RETRY             -> LOADING, pending=true, same request_id,
 *                                 same saved_input
 *   LOADING + RETRY             -> LOADING (same request_id/input kept)
 *   BLOCKED + RESET             -> READY
 *   LIMIT_REACHED + RESET       -> READY
 *   NO_MATERIALS + RESET        -> READY
 *   OFF_TOPIC + RESET           -> READY
 *   READY   + RESET             -> READY
 *   LOADING + RESET             -> LOADING
 *
 * Side-effect constraints (must hold in the implementation):
 *   - pure: no window/document/location/fetch/XMLHttpRequest/EventSource
 *   - no navigation, no streaming/async
 *   - sources stored verbatim as safe objects (rendered as text only)
 */

const { ChatStateReducer } = require('../../ai_tutor_xblock/static/js/ai_tutor.js');

const SAMPLE_REQUEST_ID = '550e8400-e29b-41d4-a716-446655440000';
const SAMPLE_INPUT = "Чому при множенні двох від'ємних чисел виходить додатне число?";

const loadingState = (overrides = {}) => ({
  ui: 'LOADING',
  pending: false,
  request_id: null,
  saved_input: '',
  message: null,
  sources: [],
  ...overrides,
});

describe('ChatStateReducer (FR-002-01)', () => {
  describe('initial state', () => {
    test('starts in LOADING, not pending, no request_id', () => {
      const s = ChatStateReducer.createInitialState();
      expect(s.ui).toBe('LOADING');
      expect(s.pending).toBe(false);
      expect(s.request_id).toBe(null);
      expect(s.saved_input).toBe('');
      expect(s.sources).toEqual([]);
    });
  });

  describe('materials transition', () => {
    test('LOADING + MATERIALS_READY -> READY with announcement', () => {
      const s = ChatStateReducer.reduce(loadingState(), { type: 'MATERIALS_READY' });
      expect(s.ui).toBe('READY');
      expect(s.pending).toBe(false);
      expect(typeof s.message).toBe('string');
      expect(s.message.length).toBeGreaterThan(0);
    });
  });

  describe('ask terminal transitions', () => {
    test('LOADING + ASK_SUCCESS -> READY with sources from payload', () => {
      const sources = [{
        segment_id: 's1', kind: 'transcript',
        source_ref: 'video@00:00', excerpt: 'При множенні…',
      }];
      const s = ChatStateReducer.reduce(
        loadingState({ pending: true }),
        { type: 'ASK_SUCCESS', payload: { sources } },
      );
      expect(s.ui).toBe('READY');
      expect(s.pending).toBe(false);
      expect(s.sources).toEqual(sources);
      expect(typeof s.message).toBe('string');
    });

    test('LOADING + ASK_BLOCKED -> BLOCKED with safe rule message', () => {
      const s = ChatStateReducer.reduce(
        loadingState({ pending: true }),
        { type: 'ASK_BLOCKED', payload: { message: 'Допомагаю розібратися, а не розв\'язую за тебе.' } },
      );
      expect(s.ui).toBe('BLOCKED');
      expect(s.message).toBe('Допомагаю розібратися, а не розв\'язую за тебе.');
      expect(s.sources).toEqual([]);
    });

    test('LOADING + ASK_NO_MATERIALS -> NO_MATERIALS', () => {
      const s = ChatStateReducer.reduce(loadingState(), { type: 'ASK_NO_MATERIALS' });
      expect(s.ui).toBe('NO_MATERIALS');
    });

    test('LOADING + ASK_OFF_TOPIC -> OFF_TOPIC', () => {
      const s = ChatStateReducer.reduce(loadingState(), { type: 'ASK_OFF_TOPIC' });
      expect(s.ui).toBe('OFF_TOPIC');
    });

    test('LOADING + ASK_LIMIT_REACHED -> LIMIT_REACHED', () => {
      const s = ChatStateReducer.reduce(loadingState(), { type: 'ASK_LIMIT_REACHED' });
      expect(s.ui).toBe('LIMIT_REACHED');
    });

    test('LOADING + ASK_ERROR -> ERROR', () => {
      const s = ChatStateReducer.reduce(loadingState(), { type: 'ASK_ERROR' });
      expect(s.ui).toBe('ERROR');
    });
  });

  describe('submit', () => {
    test('READY + SUBMIT -> LOADING, saves request_id and input', () => {
      const s = ChatStateReducer.reduce(
        loadingState({ ui: 'READY' }),
        { type: 'SUBMIT', payload: { request_id: SAMPLE_REQUEST_ID, input: SAMPLE_INPUT } },
      );
      expect(s.ui).toBe('LOADING');
      expect(s.pending).toBe(true);
      expect(s.request_id).toBe(SAMPLE_REQUEST_ID);
      expect(s.saved_input).toBe(SAMPLE_INPUT);
    });

    test('duplicate SUBMIT while pending is ignored', () => {
      const s = ChatStateReducer.reduce(
        loadingState({
          pending: true,
          request_id: SAMPLE_REQUEST_ID,
          saved_input: SAMPLE_INPUT,
        }),
        { type: 'SUBMIT', payload: { request_id: 'other-id', input: 'other' } },
      );
      expect(s.ui).toBe('LOADING');
      expect(s.pending).toBe(true);
      expect(s.request_id).toBe(SAMPLE_REQUEST_ID);
      expect(s.saved_input).toBe(SAMPLE_INPUT);
    });
  });

  describe('retry', () => {
    test('ERROR + RETRY -> LOADING with same request_id and saved input', () => {
      const s = ChatStateReducer.reduce(
        loadingState({
          ui: 'ERROR', request_id: SAMPLE_REQUEST_ID, saved_input: SAMPLE_INPUT,
        }),
        { type: 'RETRY' },
      );
      expect(s.ui).toBe('LOADING');
      expect(s.pending).toBe(true);
      expect(s.request_id).toBe(SAMPLE_REQUEST_ID);
      expect(s.saved_input).toBe(SAMPLE_INPUT);
    });

    test('LOADING + RETRY keeps request_id and input', () => {
      const s = ChatStateReducer.reduce(
        loadingState({
          pending: true, request_id: SAMPLE_REQUEST_ID, saved_input: SAMPLE_INPUT,
        }),
        { type: 'RETRY' },
      );
      expect(s.ui).toBe('LOADING');
      expect(s.request_id).toBe(SAMPLE_REQUEST_ID);
      expect(s.saved_input).toBe(SAMPLE_INPUT);
    });
  });

  describe('reset/reformulate', () => {
    test.each(['BLOCKED', 'LIMIT_REACHED', 'NO_MATERIALS', 'OFF_TOPIC'])(
      '%s + RESET -> READY', (ui) => {
        const s = ChatStateReducer.reduce(
          loadingState({ ui, message: 'some' }),
          { type: 'RESET' },
        );
        expect(s.ui).toBe('READY');
      },
    );

    test('READY + RESET -> READY', () => {
      const s = ChatStateReducer.reduce(
        loadingState({ ui: 'READY' }), { type: 'RESET' },
      );
      expect(s.ui).toBe('READY');
    });

    test('LOADING + RESET -> LOADING', () => {
      const s = ChatStateReducer.reduce(loadingState(), { type: 'RESET' });
      expect(s.ui).toBe('LOADING');
    });
  });

  describe('unknown events are ignored', () => {
    test('unmapped event returns the same state', () => {
      const state = loadingState({ ui: 'READY' });
      const s = ChatStateReducer.reduce(state, { type: 'WEIRD_EVENT' });
      expect(s).toEqual(state);
    });
  });

  describe('purity: no navigation/streaming side effects', () => {
    const forbidden = /\b(window|document|location|fetch|XMLHttpRequest|EventSource)\b/;
    test('reducer source has no window/document/location/fetch/XHR/EventSource', () => {
      const src = ChatStateReducer.reduce.toString();
      expect(src).not.toMatch(forbidden);
    });
  });

  describe('safe source refs', () => {
    test('sources keep exactly the allowed keys', () => {
      const sources = [{
        segment_id: 's1', kind: 'transcript',
        source_ref: 'video@00:00', excerpt: 'При множенні…',
      }];
      const s = ChatStateReducer.reduce(
        loadingState(),
        { type: 'ASK_SUCCESS', payload: { sources } },
      );
      expect(s.sources).toHaveLength(1);
      expect(Object.keys(s.sources[0]).sort()).toEqual(
        ['excerpt', 'kind', 'segment_id', 'source_ref'].sort(),
      );
    });
  });
});
