// verifies: FR-001-14
'use strict';

const vm = require('node:vm');
const fs = require('fs');
const path = require('path');

/**
 * Expected CommonJS API (pure functions, no DOM, no network).
 *
 * shouldFireComplete(currentTime, duration, threshold) -> boolean
 *   true iff duration > 0 && currentTime / duration >= threshold.
 *
 * accumulateWatched(watched, lastTime, currentTime, seeked) -> {watched, lastTime}
 *   Normal timeupdate adds currentTime - lastTime (delta >= 0).
 *   If seeked === true or currentTime < lastTime, watched does not grow;
 *   lastTime updates to currentTime so that subsequent deltas are measured
 *   from the new position.
 *
 * createSession(threshold) -> SessionState
 *   Creates a fresh viewing session with the given threshold.
 *   State: {threshold, watched, lastTime, completeFired}.
 *
 * evaluateTimeupdate(session, currentTime, duration) -> {session, fireComplete}
 *   Pure reduction over a single timeupdate event.
 *   - Accumulates watched time since the last reported position.
 *   - Updates session.lastTime to currentTime.
 *   - Sets fireComplete=true only the first time in a session when
 *     accumulated watched time reaches the threshold fraction of duration.
 *   - Never mutates the input session; returns a new session object.
 *
 * endSession(session) -> SessionState
 *   Ends the current session (e.g. after the 'ended' player event). Returns a
 *   fresh session with the same threshold and zero accumulated watched time,
 *   so that replay can fire a second complete event (US3.4).
 */

const {
  shouldFireComplete,
  accumulateWatched,
  createSession,
  evaluateTimeupdate,
  endSession,
  initBunnyPlayer,
} = require('../../video_xblock/static/js/student/bunny_player.js');

const THRESHOLD = 0.95;
const DURATION = 100;

const times = (duration, fraction) => duration * fraction;

describe('shouldFireComplete threshold check', () => {
  test.each([
    {current: times(DURATION, 0.97), expected: true, label: '0.97*D fires'},
    {current: times(DURATION, 0.40), expected: false, label: '0.40*D does not fire'},
    {current: times(DURATION, 0.95), expected: true, label: 'exact threshold 0.95*D fires'},
    {current: times(DURATION, 0.94), expected: false, label: '0.94*D below threshold does not fire'},
  ])('$label', ({current, expected}) => {
    expect(shouldFireComplete(current, DURATION, THRESHOLD)).toBe(expected);
  });

  test('duration <= 0 never fires', () => {
    expect(shouldFireComplete(times(DURATION, 0.97), 0, THRESHOLD)).toBe(false);
    expect(shouldFireComplete(times(DURATION, 0.97), -10, THRESHOLD)).toBe(false);
  });
});

describe('accumulateWatched', () => {
  test('normal timeupdate adds non-negative delta and moves lastTime', () => {
    expect(accumulateWatched(0, 0, 30, false)).toEqual({watched: 30, lastTime: 30});
    expect(accumulateWatched(30, 30, 55, false)).toEqual({watched: 55, lastTime: 55});
  });

  test('seeked jump near the end does not accumulate watched time', () => {
    const nearEnd = times(DURATION, 0.99);
    expect(accumulateWatched(0, 0, nearEnd, true)).toEqual({watched: 0, lastTime: nearEnd});
    expect(
      shouldFireComplete(
        accumulateWatched(0, 0, nearEnd, true).watched,
        DURATION,
        THRESHOLD,
      ),
    ).toBe(false);
  });

  test('jump backward resets lastTime without growing watched time', () => {
    expect(accumulateWatched(40, 60, 20, false)).toEqual({watched: 40, lastTime: 20});
  });
});

describe('session deduplication', () => {
  test('fires complete once when accumulated watched crosses threshold', () => {
    let state = createSession(THRESHOLD);

    // First segment: 0 -> 30 seconds watched.
    ({session: state} = evaluateTimeupdate(state, 30, DURATION));
    expect(shouldFireComplete(state.watched, DURATION, THRESHOLD)).toBe(false);

    // Second segment: 30 -> 96; crosses 95 % threshold.
    let result;
    ({session: state, fireComplete: result} = evaluateTimeupdate(state, 96, DURATION));
    expect(result).toBe(true);

    // Subsequent timeupdate in the same session must not fire again.
    ({session: state, fireComplete: result} = evaluateTimeupdate(state, 97, DURATION));
    expect(result).toBe(false);

    // A seek backwards also does not fire again.
    ({session: state, fireComplete: result} = evaluateTimeupdate(state, 50, DURATION));
    expect(result).toBe(false);
  });

  test('replay after ended creates a new session and fires a second complete', () => {
    // Normal full view fires one complete.
    let state = createSession(THRESHOLD);
    let result;
    ({session: state, fireComplete: result} = evaluateTimeupdate(state, 97, DURATION));
    expect(result).toBe(true);

    // End the session (e.g. player 'ended' event), then replay.
    state = endSession(state);
    ({session: state, fireComplete: result} = evaluateTimeupdate(state, 97, DURATION));
    expect(result).toBe(true);

    // Two complete events for one instance of the player = US3.4.
  });
});

describe('initBunnyPlayer bridge wiring', () => {
  let container;
  let handlers;
  let player;
  let request;
  let fallback;

  function makeContainer() {
    return {
      dataset: {},
      querySelector: jest.fn(() => ({tagName: 'iframe'})),
      innerHTML: '<iframe></iframe>',
    };
  }

  function makePlayerjsApi() {
    handlers = {};
    player = {
      on: jest.fn((event, callback) => {
        handlers[event] = callback;
      }),
      getDuration: jest.fn(() => DURATION),
      getCurrentTime: jest.fn(),
    };
    return {
      Player: jest.fn(() => player),
    };
  }

  beforeEach(() => {
    request = jest.fn(async () => ({result: 'success'}));
    fallback = {showUnavailable: jest.fn()};
    container = makeContainer();
  });

  function flushPromises() {
    return new Promise((resolve) => setImmediate(resolve));
  }

  function completeCalls() {
    return request.mock.calls.filter(([payload]) => payload.event_type === 'complete');
  }

  test('initBunnyPlayer returns player and getState', () => {
    const result = initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    expect(result).toEqual(expect.objectContaining({
      player: expect.any(Object),
      getState: expect.any(Function),
    }));
    expect(result.getState().threshold).toBe(THRESHOLD);
  });

  test('play event sends save_event play with current_time', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.play({seconds: 12, duration: DURATION});
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({event_type: 'play', current_time: 12}),
    );
  });

  test('pause event sends save_event pause with current_time', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.pause({seconds: 45, duration: DURATION});
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({event_type: 'pause', current_time: 45}),
    );
  });

  test('timeupdate 0 -> 30 -> 96 fires exactly one complete', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.timeupdate({seconds: 0, duration: DURATION});
    handlers.timeupdate({seconds: 30, duration: DURATION});
    handlers.timeupdate({seconds: 96, duration: DURATION});
    await flushPromises();
    expect(completeCalls()).toHaveLength(1);
    expect(completeCalls()[0][0]).toMatchObject({
      event_type: 'complete',
      current_time: 96,
      duration: DURATION,
    });
  });

  test('subsequent timeupdate or backward seek does not repeat complete', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.timeupdate({seconds: 0, duration: DURATION});
    handlers.timeupdate({seconds: 96, duration: DURATION});
    handlers.timeupdate({seconds: 97, duration: DURATION});
    handlers.timeupdate({seconds: 50, duration: DURATION});
    await flushPromises();
    expect(completeCalls()).toHaveLength(1);
  });

  test('seeked event prevents the jumped interval from counting as watched', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.timeupdate({seconds: 0, duration: DURATION});
    handlers.timeupdate({seconds: 50, duration: DURATION});
    handlers.seeked({seconds: 96});
    handlers.timeupdate({seconds: 98, duration: DURATION});
    await flushPromises();
    expect(completeCalls()).toHaveLength(0);
  });

  test('error event shows unavailable UI and emits no tracking', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.error();
    await flushPromises();
    expect(fallback.showUnavailable).toHaveBeenCalledWith(container, 'Відео недоступне');
    expect(request).not.toHaveBeenCalled();
  });

  test('ended closes session; replay to threshold fires a second complete (US3.4)', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: THRESHOLD,
    });
    handlers.timeupdate({seconds: 0, duration: DURATION});
    handlers.timeupdate({seconds: 96, duration: DURATION});
    await flushPromises();
    expect(completeCalls()).toHaveLength(1);

    handlers.ended();
    handlers.timeupdate({seconds: 0, duration: DURATION});
    handlers.timeupdate({seconds: 96, duration: DURATION});
    await flushPromises();
    expect(completeCalls()).toHaveLength(2);
  });

  test('bridge uses threshold from options', async () => {
    initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      request,
      playerjsApi: makePlayerjsApi(),
      fallback,
      threshold: 0.5,
    });
    handlers.timeupdate({seconds: 0, duration: DURATION});
    handlers.timeupdate({seconds: 51, duration: DURATION});
    await flushPromises();
    expect(completeCalls()).toHaveLength(1);
  });

  test('initBunnyPlayer does not set initialized flag when Player API is missing', () => {
    const container = makeContainer();
    container.dataset.saveEventUrl = '/handler/save_event';
    const result = initBunnyPlayer({
      container,
      saveEventUrl: '/handler/save_event',
      playerjsApi: {},
      threshold: THRESHOLD,
    });
    expect(result).toBeNull();
    expect(container.dataset.bunnyPlayerInitialized).toBeUndefined();
  });
});

describe('browser self-init via UMD', () => {
  const sourcePath = path.resolve(__dirname, '../../video_xblock/static/js/student/bunny_player.js');
  let source;

  beforeAll(() => {
    source = fs.readFileSync(sourcePath, 'utf8');
  });

  function makeSandbox() {
    const handlers = {};
    const playerInstance = {
      on: jest.fn((event, callback) => {
        handlers[event] = callback;
      }),
      getDuration: jest.fn(() => DURATION),
      getCurrentTime: jest.fn(() => 0),
    };
    const playerMock = jest.fn(() => playerInstance);
    const container = {
      dataset: {
        completionThreshold: '0.95',
        saveEventUrl: '/handler/save_event',
      },
      querySelector: jest.fn(() => ({tagName: 'iframe'})),
      innerHTML: '<iframe></iframe>',
    };
    return {
      window: {
        playerjs: {Player: playerMock},
      },
      document: {
        querySelector: jest.fn(() => container),
      },
      console,
      __refs: {handlers, playerMock, playerInstance, container},
    };
  }

  test('first script run self-initializes without ReferenceError', () => {
    const sandbox = makeSandbox();
    expect(() => { vm.runInNewContext(source, sandbox); }).not.toThrow();
    expect(sandbox.window.bunnyPlayer).toBeDefined();
    expect(sandbox.window.bunnyPlayer.initBunnyPlayerFromContainer).toEqual(expect.any(Function));
    expect(sandbox.__refs.playerMock).toHaveBeenCalledTimes(1);
    expect(sandbox.__refs.container.dataset.bunnyPlayerInitialized).toBe('1');
  });

  test('second script run in same sandbox is idempotent', () => {
    const sandbox = makeSandbox();
    vm.runInNewContext(source, sandbox);
    vm.runInNewContext(source, sandbox);
    expect(sandbox.__refs.playerMock).toHaveBeenCalledTimes(1);
    const onCalls = sandbox.__refs.playerInstance.on.mock.calls.length;
    expect(onCalls).toBe(7); // ready, play, pause, timeupdate, seeked, ended, error
  });

  test('container without completionThreshold aborts silently and does not create player', () => {
    const sandbox = makeSandbox();
    delete sandbox.__refs.container.dataset.completionThreshold;
    expect(() => { vm.runInNewContext(source, sandbox); }).not.toThrow();
    expect(sandbox.window.bunnyPlayer).toBeDefined();
    expect(sandbox.__refs.playerMock).not.toHaveBeenCalled();
  });
});
