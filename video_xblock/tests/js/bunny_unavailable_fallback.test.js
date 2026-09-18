// verifies: FR-001-10
'use strict';

const {
  showUnavailable,
  initEmbedErrorFallback,
} = require('../../video_xblock/static/js/student/bunny_unavailable_fallback.js');

describe('bunny_unavailable_fallback', () => {
  let handlers;
  let doc;
  let createdElement;

  beforeEach(() => {
    handlers = {};
    createdElement = null;
    doc = {
      createElement: jest.fn((tagName) => {
        createdElement = { tagName: tagName, className: '', textContent: '' };
        return createdElement;
      }),
    };
  });

  test('showUnavailable clears container and appends unavailable div', () => {
    const container = {
      innerHTML: '<iframe></iframe>',
      appendChild: jest.fn(),
      ownerDocument: doc,
    };
    showUnavailable(container, 'Відео недоступне');
    expect(container.innerHTML).toBe('');
    expect(doc.createElement).toHaveBeenCalledWith('div');
    expect(createdElement.className).toBe('bunny-video-unavailable');
    expect(createdElement.textContent).toBe('Відео недоступне');
    expect(container.appendChild).toHaveBeenCalledWith(createdElement);
  });

  test('initEmbedErrorFallback without iframe is a no-op', () => {
    const container = {
      querySelector: jest.fn(() => null),
      ownerDocument: doc,
    };
    expect(() => {
      initEmbedErrorFallback({
        container: container,
        messageText: 'Відео недоступне',
        playerjsApi: { Player: jest.fn() },
      });
    }).not.toThrow();
    expect(container.querySelector).toHaveBeenCalledWith('iframe');
  });

  test('initEmbedErrorFallback subscribes only to error and shows message on error', () => {
    const iframe = { name: 'iframe' };
    const container = {
      querySelector: jest.fn(() => iframe),
      appendChild: jest.fn(),
      innerHTML: '<iframe></iframe>',
      ownerDocument: doc,
    };
    const Player = jest.fn(() => ({
      on: jest.fn((event, callback) => {
        handlers[event] = callback;
      }),
    }));
    initEmbedErrorFallback({
      container: container,
      messageText: 'Відео недоступне',
      playerjsApi: { Player: Player },
    });
    expect(Player).toHaveBeenCalledTimes(1);
    expect(Player).toHaveBeenCalledWith(iframe);
    expect(Object.keys(handlers)).toEqual(['error']);
    expect(handlers.error).toEqual(expect.any(Function));
    handlers.error();
    expect(container.innerHTML).toBe('');
    expect(createdElement.className).toBe('bunny-video-unavailable');
    expect(createdElement.textContent).toBe('Відео недоступне');
  });

  test('the error handler does not emit tracking events or subscribe to other events', () => {
    const iframe = {};
    const container = {
      querySelector: jest.fn(() => iframe),
      appendChild: jest.fn(),
      innerHTML: '<iframe></iframe>',
      ownerDocument: doc,
    };
    const playerOn = jest.fn((event, callback) => {
      handlers[event] = callback;
    });
    const Player = jest.fn(() => ({ on: playerOn }));
    initEmbedErrorFallback({
      container: container,
      messageText: 'Відео недоступне',
      playerjsApi: { Player: Player },
    });
    expect(playerOn).toHaveBeenCalledTimes(1);
    expect(playerOn).toHaveBeenCalledWith('error', expect.any(Function));
    expect(Object.keys(handlers)).toEqual(['error']);
    handlers.error();
    expect(playerOn).toHaveBeenCalledTimes(1);
  });

  test('CommonJS require має нуль side effects навіть з document/window у глобалах', () => {
    const resolved = require.resolve('../../video_xblock/static/js/student/bunny_unavailable_fallback.js');
    global.document = { querySelector: jest.fn() };
    global.window = { playerjs: { Player: jest.fn() } };
    delete require.cache[resolved];
    try {
      const mod = require(resolved);
      expect(global.document.querySelector).not.toHaveBeenCalled();
      expect(global.window.playerjs.Player).not.toHaveBeenCalled();
      expect(typeof mod.showUnavailable).toBe('function');
      expect(typeof mod.initEmbedErrorFallback).toBe('function');
    } finally {
      delete global.document;
      delete global.window;
      delete require.cache[resolved];
    }
  });

  test('browser self-init ідемпотентний при повторному завантаженні скрипта', () => {
    const fs = require('fs');
    const vm = require('node:vm');
    const resolved = require.resolve('../../video_xblock/static/js/student/bunny_unavailable_fallback.js');
    const code = fs.readFileSync(resolved, 'utf-8');

    const handlers = {};
    const attributes = {};
    const iframe = { name: 'iframe' };
    const container = {
      querySelector: jest.fn((selector) => (selector === 'iframe' ? iframe : null)),
      getAttribute: jest.fn((name) => attributes[name]),
      setAttribute: jest.fn((name, value) => {
        attributes[name] = value;
      }),
      appendChild: jest.fn(),
      innerHTML: '<iframe></iframe>',
      ownerDocument: {
        createElement: jest.fn(() => ({ tagName: 'div', className: '', textContent: '' })),
      },
    };

    const Player = jest.fn(() => ({
      on: jest.fn((event, callback) => {
        handlers[event] = callback;
      }),
    }));

    const sandbox = {
      window: {
        playerjs: { Player: Player },
      },
      document: {
        querySelector: jest.fn(() => container),
      },
      console: console,
    };

    vm.runInNewContext(code, sandbox);
    vm.runInNewContext(code, sandbox);

    expect(Player).toHaveBeenCalledTimes(1);
    expect(Player).toHaveBeenCalledWith(iframe);
    expect(Object.keys(handlers)).toEqual(['error']);
    expect(sandbox.window.bunnyUnavailableFallback).toBeDefined();
    expect(typeof sandbox.window.bunnyUnavailableFallback.showUnavailable).toBe('function');
    expect(typeof sandbox.window.bunnyUnavailableFallback.initEmbedErrorFallback).toBe('function');
  });
});
