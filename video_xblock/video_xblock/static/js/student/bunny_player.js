// impl: FR-001-14
'use strict';

(function (global, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    // CommonJS / Jest: expose pure functions and the bridge entry point without
    // touching document or window (constitution II, FR-001-14).
    module.exports = factory();
  } else if (typeof window !== 'undefined') {
    var api = factory();
    window.bunnyPlayer = api;

    if (typeof document !== 'undefined') {
      var container = document.querySelector('.bunny-video-player');
      if (container) {
        api.initBunnyPlayerFromContainer(container);
      }
    }
  }
})(this, function () {
  /**
   * Pure threshold check used by the timeupdate bridge.
   * A complete event fires only when the watched fraction reaches the configured
   * threshold, never just because the playhead touched the end.
   */
  function shouldFireComplete(currentTime, duration, threshold) {
    return duration > 0 && currentTime / duration >= threshold;
  }

  /**
   * Accumulate watched seconds between two timeupdate positions.
   * Backward jumps and explicit seeks do not increase watched time; they only
   * reset the reference point so that future progress is measured correctly.
   */
  function accumulateWatched(watched, lastTime, currentTime, seeked) {
    if (seeked || currentTime < lastTime) {
      return {watched: watched, lastTime: currentTime};
    }
    return {watched: watched + (currentTime - lastTime), lastTime: currentTime};
  }

  /**
   * Create a fresh viewing session for one student-view load.
   */
  function createSession(threshold) {
    return {
      threshold: threshold,
      watched: 0,
      lastTime: 0,
      completeFired: false,
    };
  }

  /**
   * Pure reduction over a single timeupdate event.
   * Returns a new session object; the input session is never mutated.
   */
  function evaluateTimeupdate(session, currentTime, duration) {
    var accumulated = accumulateWatched(
      session.watched,
      session.lastTime,
      currentTime,
      false,
    );
    var fireComplete = false;
    if (!session.completeFired && shouldFireComplete(
      accumulated.watched,
      duration,
      session.threshold,
    )) {
      fireComplete = true;
    }
    return {
      session: {
        threshold: session.threshold,
        watched: accumulated.watched,
        lastTime: accumulated.lastTime,
        completeFired: fireComplete || session.completeFired,
      },
      fireComplete: fireComplete,
    };
  }

  /**
   * End the current session, e.g. on the player 'ended' event.
   * The returned session preserves the threshold but resets progress, so a
   * replay can fire a second complete event (US3.4).
   */
  function endSession(session) {
    return createSession(session.threshold);
  }

  /**
   * Default request implementation for the browser: POST JSON to the handler
   * URL and ignore the response silently. The primary recording happens on the
   * server via save_event (T-035).
   */
  function buildRequest(saveEventUrl) {
    return function request(payload) {
      if (typeof fetch === 'function') {
        fetch(saveEventUrl, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        }).catch(function () {});
      }
    };
  }

  /**
   * Wire a player.js instance to the XBlock save_event handler.
   * All heavy logic stays in the pure functions above; this function only deals
   * with event plumbing and UI-fallback delegation.
   */
  function initBunnyPlayer(options) {
    var container = options.container;
    var playerjsApi = options.playerjsApi;
    var fallback = options.fallback;

    var saveEventUrl = options.saveEventUrl;
    if (!saveEventUrl && container && container.dataset) {
      saveEventUrl = container.dataset.saveEventUrl;
    }

    var threshold = options.threshold;
    if (typeof threshold !== 'number' && container && container.dataset) {
      threshold = parseFloat(container.dataset.completionThreshold);
    }

    // Fail-safe: without a valid threshold or handler URL, tracking events
    // must not be emitted (FR-001-14).
    if (typeof threshold !== 'number' || isNaN(threshold) || threshold <= 0 || threshold > 1) {
      return null;
    }
    if (!saveEventUrl) {
      return null;
    }

    // Idempotent initialisation: reloading the script must not duplicate
    // subscriptions on the same player.
    if (container && container.dataset && container.dataset.bunnyPlayerInitialized === '1') {
      return null;
    }

    var Player = playerjsApi && playerjsApi.Player;
    if (typeof Player !== 'function') {
      return null;
    }

    var iframe = container && typeof container.querySelector === 'function' ?
      container.querySelector('iframe') : null;
    if (!iframe) {
      return null;
    }

    if (container && container.dataset) {
      container.dataset.bunnyPlayerInitialized = '1';
    }

    var player = new Player(iframe);
    var session = createSession(threshold);

    var requestImpl = options.request;
    if (typeof requestImpl !== 'function') {
      requestImpl = buildRequest(saveEventUrl);
    }

    function getDuration(data) {
      if (data && typeof data.duration === 'number') {
        return data.duration;
      }
      if (player && typeof player.getDuration === 'function') {
        return player.getDuration();
      }
      return 0;
    }

    function getCurrentTime(data) {
      if (data && typeof data.seconds === 'number') {
        return data.seconds;
      }
      if (data && typeof data.currentTime === 'number') {
        return data.currentTime;
      }
      if (player && typeof player.getCurrentTime === 'function') {
        return player.getCurrentTime();
      }
      return 0;
    }

    function sendEvent(eventType, data) {
      var payload = {
        event_type: eventType,
        current_time: getCurrentTime(data),
      };
      var duration = getDuration(data);
      if (duration > 0) {
        payload.duration = duration;
      }
      requestImpl(payload);
    }

    function onTimeupdate(data) {
      var seconds = getCurrentTime(data);
      var duration = getDuration(data);
      var result = evaluateTimeupdate(session, seconds, duration);
      session = result.session;
      if (result.fireComplete) {
        sendEvent('complete', {seconds: seconds, duration: duration});
      }
    }

    function onSeeked(data) {
      var seconds = getCurrentTime(data);
      var updated = accumulateWatched(session.watched, session.lastTime, seconds, true);
      session = {
        threshold: session.threshold,
        watched: updated.watched,
        lastTime: updated.lastTime,
        completeFired: session.completeFired,
      };
    }

    function onEnded() {
      session = endSession(session);
    }

    function onError() {
      if (fallback && typeof fallback.showUnavailable === 'function' && container) {
        fallback.showUnavailable(container, 'Відео недоступне');
      }
    }

    if (player && typeof player.on === 'function') {
      player.on('ready', function () {});
      player.on('play', function (data) { sendEvent('play', data); });
      player.on('pause', function (data) { sendEvent('pause', data); });
      player.on('timeupdate', onTimeupdate);
      player.on('seeked', onSeeked);
      player.on('ended', onEnded);
      player.on('error', onError);
    }

    return {
      player: player,
      getState: function () {
        return {
          threshold: session.threshold,
          watched: session.watched,
          lastTime: session.lastTime,
          completeFired: session.completeFired,
        };
      },
    };
  }

  /**
   * Browser self-init from a rendered .bunny-video-player container.
   * Reads the threshold and handler URL from data attributes, then delegates to
   * initBunnyPlayer. Missing/invalid data attributes abort silently without
   * emitting any events.
   */
  function initBunnyPlayerFromContainer(container) {
    if (!container || !container.dataset) {
      return;
    }
    var threshold = parseFloat(container.dataset.completionThreshold);
    if (!isFinite(threshold)) {
      return;
    }
    var saveEventUrl = container.dataset.saveEventUrl;
    if (!saveEventUrl) {
      return;
    }
    var playerjsApi = (typeof window !== 'undefined' && window.playerjs) ? window.playerjs : {};
    var fallback = null;
    if (typeof window !== 'undefined' && window.bunnyUnavailableFallback) {
      fallback = window.bunnyUnavailableFallback;
    }

    initBunnyPlayer({
      container: container,
      saveEventUrl: saveEventUrl,
      playerjsApi: playerjsApi,
      fallback: fallback,
      threshold: threshold,
    });
  }

  return {
    shouldFireComplete: shouldFireComplete,
    accumulateWatched: accumulateWatched,
    createSession: createSession,
    evaluateTimeupdate: evaluateTimeupdate,
    endSession: endSession,
    initBunnyPlayer: initBunnyPlayer,
    initBunnyPlayerFromContainer: initBunnyPlayerFromContainer,
  };
});
