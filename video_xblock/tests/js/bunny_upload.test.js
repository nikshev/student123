// verifies: FR-001-02, FR-001-03, FR-001-04
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const YAML = require('yaml');

// Expected future CommonJS API (no DOM required):
// validateFile(file, config) -> null | {code, message}; pure, no input mutation.
// buildTusOptions(file, createUploadResponse) -> {endpoint, headers, metadata}.
// startUpload({file, config, request, videoId?}) -> Promise<started tus.Upload>.
// request(handlerName, JSONBody) -> Promise<handler response>; the only HTTP seam.
// videoId is the block's persisted GUID; refresh credentials before resume/start.
// TUS onError returns the recovery Promise so callers can await a 404 restart.
// Fresh/replacement uploads must not reuse a stale previous upload URL.
// Optional progressElement is a native <progress>; onProgress(percent, sent, total).
// onError receives a safe Error with .cause; startup failures also reject.
// TUS callback failures resolve to that Error (also on upload.error), avoiding
// unhandled rejections when tus ignores callback return values.

jest.mock('tus-js-client', () => ({Upload: jest.fn()}), {virtual: true});
const tus = require('tus-js-client');

// Parse the real, versioned YAML, not a regex or duplicated policy constants.
const config = YAML.parse(fs.readFileSync(
  path.resolve(__dirname, '../../video_xblock/bunny_config.yaml'), 'utf8'
));
Object.freeze(config.allowed_extensions);
Object.freeze(config);

const {
  validateFile,
  buildTusOptions,
  startUpload,
} = require('../../video_xblock/static/js/studio/bunny_upload.js');

// Static handler/TUS fixtures: identifiers and signatures are synthetic, no secrets.
const file = Object.freeze({name: 'Лекція 1.mp4', type: 'video/mp4', size: 104857600});
const created = Object.freeze({
  video_id: '32d140e2-e4f4-4eec-9d53-20371e9be607',
  library_id: 759,
  tus_endpoint: config.tus_endpoint,
  authorization_signature: 'a'.repeat(64),
  authorization_expire: 1750000000,
});
const refreshed = Object.freeze({
  ...created,
  authorization_signature: 'b'.repeat(64),
  authorization_expire: created.authorization_expire + config.upload_auth_ttl_seconds,
});
const replacement = Object.freeze({
  ...refreshed,
  video_id: 'a8f1c692-7e28-4b9d-a516-4f18a0aa9156',
  authorization_signature: 'c'.repeat(64),
});
const previous = Object.freeze({
  size: file.size,
  metadata: {filetype: file.type, title: file.name},
  creationTime: '2025-06-15T12:00:00.000Z',
  uploadUrl: `${config.tus_endpoint}/recorded-partial-upload`,
  urlStorageKey: 'tus::recorded-file-fingerprint::partial-upload',
});
const requestBody = {file_name: file.name, file_size: file.size, file_type: file.type};

let uploads;
let request;
let fetchGuard;

beforeEach(() => {
  uploads = [];
  request = jest.fn(async (handler) => {
    throw new Error(`Unexpected handler request: ${handler}`);
  });
  fetchGuard = jest.spyOn(globalThis, 'fetch').mockImplementation(() => {
    throw new Error('Network forbidden: use the injected request fixture');
  });
  jest.spyOn(require('node:http'), 'request').mockImplementation(() => {
    throw new Error('Network forbidden');
  });
  jest.spyOn(require('node:https'), 'request').mockImplementation(() => {
    throw new Error('Network forbidden');
  });
  tus.Upload.mockReset();
  tus.Upload.mockImplementation(function (inputFile, options) {
    this.file = inputFile;
    this.options = options;
    this.findPreviousUploads = jest.fn().mockResolvedValue([previous]);
    this.resumeFromPreviousUpload = jest.fn();
    this.start = jest.fn();
    uploads.push(this);
  });
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('pre-validation before any request', () => {
  test.each(config.allowed_extensions)('accepts YAML-allowed .%s at the size limit without mutation', (extension) => {
    const candidate = Object.freeze({
      ...file, name: `lecture.${extension}`, size: config.max_upload_bytes,
    });
    const before = JSON.stringify({candidate, config});
    expect(validateFile(candidate, config)).toBeNull();
    expect(JSON.stringify({candidate, config})).toBe(before);
    expect(request).not.toHaveBeenCalled();
    expect(fetchGuard).not.toHaveBeenCalled();
    expect(tus.Upload).not.toHaveBeenCalled();
  });

  test('rejects an unsupported extension before create, credentials, or TUS', async () => {
    // Valid MIME isolates extension validation rather than MIME rejection.
    const candidate = Object.freeze({...file, name: 'lecture.exe'});
    const error = validateFile(candidate, config);
    expect(error).toEqual({code: 'unsupported_extension', message: expect.any(String)});
    expect(error.message.trim().length).toBeGreaterThan(0);
    expect(error.message.toLowerCase()).toContain('exe');
    await expect(startUpload({file: candidate, config, request})).rejects.toThrow(error.message);
    expect(request).not.toHaveBeenCalled();
    expect(fetchGuard).not.toHaveBeenCalled();
    expect(tus.Upload).not.toHaveBeenCalled();
  });

  test('rejects max_upload_bytes + 1 with the limit before any request, including resume', async () => {
    const candidate = Object.freeze({...file, size: config.max_upload_bytes + 1});
    const error = validateFile(candidate, config);
    expect(error).toEqual({code: 'file_too_large', message: expect.any(String)});
    // API convention: the explanatory message includes the exact byte limit.
    expect(error.message).toContain(String(config.max_upload_bytes));
    await expect(startUpload({file: candidate, config, request})).rejects.toThrow(error.message);
    await expect(startUpload({
      file: candidate, config, request, videoId: created.video_id,
    })).rejects.toThrow(error.message);
    expect(request).not.toHaveBeenCalled();
    expect(fetchGuard).not.toHaveBeenCalled();
    expect(tus.Upload).not.toHaveBeenCalled();
  });
});

describe('progress and useful errors', () => {
  test('updates injected progress and callback without requiring a DOM', async () => {
    request.mockResolvedValueOnce(created);
    const progressElement = {max: 1, value: 0};
    const onProgress = jest.fn();
    const upload = await startUpload({file, config, request, progressElement, onProgress});
    upload.options.onProgress(file.size / 2, file.size);
    expect(progressElement).toEqual({max: 100, value: 50});
    expect(onProgress).toHaveBeenLastCalledWith(50, file.size / 2, file.size);
    upload.options.onProgress(0, 0);
    expect(progressElement.value).toBe(0);
    upload.options.onProgress(file.size, file.size);
    expect(progressElement.value).toBe(100);
  });

  test('reports a safe TUS error without recreating a GUID on non-404 failures', async () => {
    request.mockResolvedValueOnce(refreshed);
    const onError = jest.fn();
    const upload = await startUpload({file, config, request, videoId: created.video_id, onError});
    const cause = Object.assign(new Error('raw signed URL must not reach UI'), {
      originalResponse: {getStatus: () => 503},
    });
    const error = await upload.options.onError(cause);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toContain('Повторіть');
    expect(error.message).not.toContain(cause.message);
    expect(error.cause).toBe(cause);
    expect(upload.error).toBe(error);
    expect(onError).toHaveBeenCalledWith(error);
    expect(request).toHaveBeenCalledTimes(1);
    expect(uploads).toHaveLength(1);
  });

  test('rejects and reports handler failures before constructing TUS', async () => {
    const cause = new Error('recorded service failure');
    request.mockRejectedValueOnce(cause);
    const onError = jest.fn();
    await expect(startUpload({file, config, request, onError})).rejects.toThrow('Повторіть');
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({cause}));
    expect(tus.Upload).not.toHaveBeenCalled();
  });

  test('reports a failed 404 replacement without an unhandled callback rejection', async () => {
    request.mockResolvedValueOnce(refreshed).mockRejectedValueOnce(new Error('service down'));
    const onError = jest.fn();
    const upload = await startUpload({file, config, request, videoId: created.video_id, onError});
    const error = await upload.options.onError({originalResponse: {getStatus: () => 404}});
    expect(error).toBeInstanceOf(Error);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(error);
    expect(uploads).toHaveLength(1);
  });
});

describe('TUS options and upload lifecycle', () => {
  test('builds exact TUS headers and filetype/title metadata from create_upload and file', () => {
    const before = JSON.stringify({file, created});
    const options = buildTusOptions(file, created);
    expect(options.endpoint).toBe(created.tus_endpoint);
    // HTTP header values are strings; no AccessKey or guessed client signature.
    expect(options.headers).toEqual({
      AuthorizationSignature: created.authorization_signature,
      AuthorizationExpire: String(created.authorization_expire),
      LibraryId: String(created.library_id),
      VideoId: created.video_id,
    });
    // create_upload returns credentials only; metadata comes from its input file.
    expect(options.metadata).toEqual({filetype: file.type, title: file.name});
    expect(JSON.stringify({file, created})).toBe(before);
    expect(request).not.toHaveBeenCalled();
    expect(tus.Upload).not.toHaveBeenCalled();
  });

  test('creates and starts a fresh upload with server-provided options', async () => {
    request.mockResolvedValueOnce(created);
    const upload = await startUpload({file, config, request});
    expect(request.mock.calls).toEqual([['create_upload', requestBody]]);
    expect(tus.Upload).toHaveBeenCalledTimes(1);
    expect(upload).toBe(uploads[0]);
    expect(upload.file).toBe(file);
    expect(upload.options).toEqual(expect.objectContaining(buildTusOptions(file, created)));
    expect(upload.resumeFromPreviousUpload).not.toHaveBeenCalled();
    expect(upload.start).toHaveBeenCalledTimes(1);
  });

  test('refreshes credentials for the same GUID and finds/resumes/starts without create_upload', async () => {
    request.mockResolvedValueOnce(refreshed);
    const upload = await startUpload({file, config, request, videoId: created.video_id});
    expect(request.mock.calls).toEqual([
      ['upload_credentials', {video_id: created.video_id}],
    ]);
    expect(tus.Upload).toHaveBeenCalledTimes(1);
    expect(upload).toBe(uploads[0]);
    expect(upload.file).toBe(file);
    expect(upload.options).toEqual(expect.objectContaining(buildTusOptions(file, refreshed)));
    expect(upload.options.headers.AuthorizationSignature).not.toBe(created.authorization_signature);
    expect(upload.options.headers.VideoId).toBe(created.video_id);
    expect(upload.findPreviousUploads).toHaveBeenCalledTimes(1);
    expect(upload.resumeFromPreviousUpload).toHaveBeenCalledWith(previous);
    expect(upload.resumeFromPreviousUpload).toHaveBeenCalledTimes(1);
    expect(upload.start).toHaveBeenCalledTimes(1);
    expect(request.mock.invocationCallOrder[0]).toBeLessThan(upload.start.mock.invocationCallOrder[0]);
    expect(upload.findPreviousUploads.mock.invocationCallOrder[0])
      .toBeLessThan(upload.resumeFromPreviousUpload.mock.invocationCallOrder[0]);
    expect(upload.resumeFromPreviousUpload.mock.invocationCallOrder[0])
      .toBeLessThan(upload.start.mock.invocationCallOrder[0]);
  });

  test('TUS onError originalResponse 404 on resume creates a new GUID and starts fresh', async () => {
    request.mockResolvedValueOnce(refreshed).mockResolvedValueOnce(replacement);
    const resumed = await startUpload({file, config, request, videoId: created.video_id});
    expect(request.mock.calls).toEqual([
      ['upload_credentials', {video_id: created.video_id}],
    ]);
    expect(resumed.resumeFromPreviousUpload).toHaveBeenCalledWith(previous);
    expect(resumed.start).toHaveBeenCalledTimes(1);
    // tus DetailedError exposes status via originalResponse, not error.status.
    const error = Object.assign(new Error('tus: unexpected response for HEAD request'), {
      originalRequest: {
        getMethod: () => 'HEAD',
        getURL: () => previous.uploadUrl,
      },
      originalResponse: {getStatus: jest.fn(() => 404)},
    });
    expect(resumed.options.onError).toEqual(expect.any(Function));
    await resumed.options.onError(error);
    expect(error.originalResponse.getStatus).toHaveBeenCalled();
    expect(request.mock.calls).toEqual([
      ['upload_credentials', {video_id: created.video_id}],
      ['create_upload', requestBody],
    ]);
    expect(tus.Upload).toHaveBeenCalledTimes(2);
    const fresh = uploads[1];
    expect(fresh.file).toBe(file);
    expect(fresh.options).toEqual(expect.objectContaining(buildTusOptions(file, replacement)));
    expect(fresh.options.headers.VideoId).toBe(replacement.video_id);
    expect(fresh.options.headers.VideoId).not.toBe(created.video_id);
    expect(fresh.options.uploadUrl).toBeUndefined();
    expect(fresh.resumeFromPreviousUpload).not.toHaveBeenCalled();
    expect(fresh.start).toHaveBeenCalledTimes(1);
    expect(resumed.start).toHaveBeenCalledTimes(1);
  });
});
