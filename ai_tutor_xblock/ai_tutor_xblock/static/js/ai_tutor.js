// impl: FR-002-01
/**
 * AI Tutor student view: pure ChatStateReducer + minimal DOM adapter.
 *
 * Contract: tests/js/ai_tutor.test.js (T-027). The reducer is a pure
 * synchronous function: no window/document/location/fetch/XHR/EventSource,
 * no navigation, no streaming. Duplicate submits are ignored while pending;
 * retry preserves request_id and saved input; terminal transitions set an
 * accessible announcement message; sources keep exactly the safe keys.
 */

const ANNOUNCEMENTS = {
    MATERIALS_READY: 'Матеріали уроку завантажено.',
    ASK_SUCCESS: 'Відповідь репетитора отримано.',
    ASK_BLOCKED: 'Відповідь заблоковано: репетитор не розв\'язує за вас.',
    ASK_NO_MATERIALS: 'У матеріалах цього уроку відповіді немає.',
    ASK_OFF_TOPIC: 'Питання поза темою цього уроку.',
    ASK_LIMIT_REACHED: 'Щоденний ліміт запитань вичерпано.',
    ASK_ERROR: 'Сталася помилка. Спробуйте ще раз.',
};

function _cleanSources(sources) {
    if (!Array.isArray(sources)) {
        return [];
    }
    return sources.map(function (source) {
        return {
            segment_id: String(source.segment_id == null ? '' : source.segment_id),
            kind: String(source.kind == null ? '' : source.kind),
            source_ref: String(source.source_ref == null ? '' : source.source_ref),
            excerpt: String(source.excerpt == null ? '' : source.excerpt),
        };
    });
}

function createInitialState() {
    return {
        ui: 'LOADING',
        pending: false,
        request_id: null,
        saved_input: '',
        message: null,
        sources: [],
    };
}

function reduce(state, event) {
    if (!state || typeof state !== 'object') {
        return createInitialState();
    }
    if (!event || typeof event !== 'object' || typeof event.type !== 'string') {
        return { ...state };
    }

    const type = event.type;
    const payload = event.payload || {};

    if (type === 'SUBMIT') {
        if (state.pending) {
            return { ...state };
        }
        if (state.ui === 'READY') {
            return {
                ...state,
                ui: 'LOADING',
                pending: true,
                request_id: payload.request_id == null ? null : String(payload.request_id),
                saved_input: payload.input == null ? '' : String(payload.input),
            };
        }
        return { ...state };
    }

    if (type === 'RETRY') {
        if (state.ui === 'ERROR' || state.ui === 'LOADING') {
            return {
                ...state,
                ui: 'LOADING',
                pending: true,
                request_id: state.request_id,
                saved_input: state.saved_input,
            };
        }
        return { ...state };
    }

    if (type === 'RESET') {
        if (state.ui === 'LOADING' || state.ui === 'READY') {
            return { ...state };
        }
        // BLOCKED / LIMIT_REACHED / NO_MATERIALS / OFF_TOPIC -> READY
        return {
            ...state,
            ui: 'READY',
            pending: false,
            message: null,
            sources: [],
        };
    }

    if (type === 'MATERIALS_READY') {
        return {
            ...state,
            ui: 'READY',
            pending: false,
            message: ANNOUNCEMENTS.MATERIALS_READY,
        };
    }

    if (type === 'ASK_SUCCESS') {
        return {
            ...state,
            ui: 'READY',
            pending: false,
            message: ANNOUNCEMENTS.ASK_SUCCESS,
            sources: _cleanSources(payload.sources),
        };
    }

    if (type === 'ASK_BLOCKED') {
        return {
            ...state,
            ui: 'BLOCKED',
            pending: false,
            message: String(payload.message || ANNOUNCEMENTS.ASK_BLOCKED),
            sources: [],
        };
    }

    if (type === 'ASK_NO_MATERIALS') {
        return {
            ...state,
            ui: 'NO_MATERIALS',
            pending: false,
            message: ANNOUNCEMENTS.ASK_NO_MATERIALS,
        };
    }

    if (type === 'ASK_OFF_TOPIC') {
        return {
            ...state,
            ui: 'OFF_TOPIC',
            pending: false,
            message: ANNOUNCEMENTS.ASK_OFF_TOPIC,
        };
    }

    if (type === 'ASK_LIMIT_REACHED') {
        return {
            ...state,
            ui: 'LIMIT_REACHED',
            pending: false,
            message: ANNOUNCEMENTS.ASK_LIMIT_REACHED,
        };
    }

    if (type === 'ASK_ERROR') {
        return {
            ...state,
            ui: 'ERROR',
            pending: false,
            message: ANNOUNCEMENTS.ASK_ERROR,
        };
    }

    return { ...state };
}

const ChatStateReducer = {
    createInitialState: createInitialState,
    reduce: reduce,
};

// ---- Minimal DOM adapter (not covered by contract tests) ----
// Attaches a submit handler to the chat form: duplicate submits are disabled
// while a request is pending and the typed question is preserved on retry.
// No navigation, no streaming, no direct service calls from the adapter.
function attachChatAdapter(rootElement, reducer) {
    if (!rootElement || typeof rootElement.querySelector !== 'function') {
        return null;
    }
    const form = rootElement.querySelector('[data-ask-url]');
    const questionInput = rootElement.querySelector('textarea, input[name="question"]');
    if (!form || !questionInput) {
        return null;
    }

    let currentState = reducer.createInitialState();

    function render(nextState) {
        currentState = nextState;
        questionInput.disabled = currentState.pending;
        const announcer = rootElement.querySelector('[aria-live]');
        if (announcer && currentState.message) {
            announcer.textContent = currentState.message;
        }
    }

    form.addEventListener('submit', function (submitEvent) {
        submitEvent.preventDefault();
        const input = questionInput.value;
        const next = reducer.reduce(currentState, {
            type: 'SUBMIT',
            payload: {
                request_id: (typeof crypto !== 'undefined' && crypto.randomUUID)
                    ? crypto.randomUUID()
                    : null,
                input: input,
            },
        });
        render(next);
    });

    render(currentState);
    return { getState: function () { return currentState; } };
}

module.exports = {
    ChatStateReducer: ChatStateReducer,
    attachChatAdapter: attachChatAdapter,
};
