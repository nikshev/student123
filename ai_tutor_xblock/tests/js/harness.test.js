// verifies: FR-002-01

/**
 * Smoke test for ai_tutor_xblock jest harness.
 *
 * Перевіряє:
 * 1. jest запускається (файл тесту знайдено і виконується).
 * 2. Мережні виклики (fetch/XMLHttpRequest) заборонені в середовищі jest.
 *
 * Очікуваний результат на цьому етапі (T-005): червоний через відсутність
 * package.json / jest.config.js і node_modules. T-006 створить конфіг,
 * після чого тест стане зеленим.
 */

'use strict';

// Глобально заборонити fetch — будь-який виклик має кидати помилку
global.fetch = () => {
  throw new Error('Network access denied: fetch is disabled in tests');
};

// Заборонити XMLHttpRequest
global.XMLHttpRequest = class XMLHttpRequest {
  constructor() {
    throw new Error('Network access denied: XMLHttpRequest is disabled in tests');
  }
  open() { throw new Error('Network access denied'); }
  send() { throw new Error('Network access denied'); }
  setRequestHeader() { throw new Error('Network access denied'); }
  addEventListener() { throw new Error('Network access denied'); }
  removeEventListener() { throw new Error('Network access denied'); }
  get readyState() { return 0; }
  get status() { return 0; }
  get responseText() { return ''; }
};

describe('Jest harness smoke tests', () => {
  test('jest runs - basic passing test', () => {
    expect(true).toBe(true);
  });

  test('fetch is globally disabled and throws', () => {
    expect(() => {
      fetch('http://example.invalid');
    }).toThrow('Network access denied: fetch is disabled in tests');
  });

  test('XMLHttpRequest is globally disabled and throws on construction', () => {
    expect(() => {
      new XMLHttpRequest();
    }).toThrow('Network access denied: XMLHttpRequest is disabled in tests');
  });

  test('XMLHttpRequest methods throw if somehow instantiated', () => {
    // На випадок, якщо хтось створив екземпляр обходом конструктора
    const xhr = Object.create(XMLHttpRequest.prototype);
    expect(() => xhr.open('GET', 'http://example.invalid')).toThrow('Network access denied');
    expect(() => xhr.send()).toThrow('Network access denied');
  });
});