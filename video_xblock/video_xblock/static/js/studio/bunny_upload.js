// impl: FR-001-02, FR-001-03, FR-001-04
(function(root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) {
        module.exports = factory(require('tus-js-client'));
    } else {
        root.BunnyUpload = factory(root.tus);
    }
}(this, function(tusClient) {
    'use strict';

    function validateFile(file, config) {
        var extension = file && /\.([^.]+)$/.exec(file.name || '');
        extension = extension ? extension[1].toLowerCase() : '';
        if (config.allowed_extensions.indexOf(extension) === -1) {
            return {
                code: 'unsupported_extension',
                message: 'Непідтримуваний формат «' + extension + '». Оберіть: ' +
                    config.allowed_extensions.join(', ') + '.'
            };
        }
        if (file.size > config.max_upload_bytes) {
            return {
                code: 'file_too_large',
                message: 'Файл перевищує ліміт ' + config.max_upload_bytes + ' байтів.'
            };
        }
        if (typeof file.type !== 'string' || file.type.indexOf('video/') !== 0) {
            return {code: 'unsupported_type', message: 'Оберіть відеофайл.'};
        }
        return null;
    }

    function buildTusOptions(file, credentials) {
        return {
            endpoint: credentials.tus_endpoint,
            headers: {
                AuthorizationSignature: credentials.authorization_signature,
                AuthorizationExpire: String(credentials.authorization_expire),
                LibraryId: String(credentials.library_id),
                VideoId: credentials.video_id
            },
            metadata: {filetype: file.type, title: file.name}
        };
    }

    // request(handler, body) is the only handler transport. Resolves once started,
    // not when transferred. Optional UI seams: progressElement, onProgress, onError.
    // Validation errors carry a `code`; only these expose their own message to
    // the UI. Any other failure (handler or TUS) maps to a generic retry text.
    function startUpload(settings) {
        var file = settings.file;

        function report(cause, validation) {
            var error = new Error(validation ? validation.message :
                'Не вдалося завантажити відео. Перевірте з’єднання. Повторіть завантаження того самого файла.');
            error.code = validation ? validation.code : 'upload_failed';
            error.cause = cause;
            if (settings.onError) {
                settings.onError(error);
            }
            return error;
        }

        function create() {
            return settings.request('create_upload', {
                file_name: file.name,
                file_size: file.size,
                file_type: file.type
            });
        }

        function launch(credentials, resume) {
            var options = buildTusOptions(file, credentials);
            var upload;
            var resumed = false;
            var recovery;

            // Use tus's browser localStorage URL store, scoped to this GUID so a
            // different block or a replacement cannot inherit a stale upload URL.
            options.storeFingerprintForResuming = true;
            options.removeFingerprintOnSuccess = true;
            options.fingerprint = function() {
                return Promise.resolve(JSON.stringify([
                    'bunny', credentials.tus_endpoint, credentials.library_id,
                    credentials.video_id, file.name, file.type, file.size, file.lastModified
                ]));
            };
            options.onProgress = function(sent, total) {
                var percent = total > 0 ? Math.max(0, Math.min(100, sent / total * 100)) : 0;
                if (settings.progressElement) {
                    settings.progressElement.max = 100;
                    settings.progressElement.value = percent;
                }
                if (settings.onProgress) {
                    settings.onProgress(percent, sent, total);
                }
            };
            options.onError = function(cause) {
                if (recovery) {
                    return recovery;
                }
                if (resumed && cause.originalResponse && cause.originalResponse.getStatus() === 404) {
                    // Never resume the replacement; its credentials have a new GUID.
                    // Return recovery for callers, but consume failures because tus
                    // itself does not await an onError callback's Promise.
                    recovery = Promise.resolve().then(create).then(function(fresh) {
                        return launch(fresh, false);
                    }).catch(function(error) {
                        upload.error = report(error);
                        return upload.error;
                    });
                    return recovery;
                }
                upload.error = report(cause);
                return Promise.resolve(upload.error);
            };
            upload = new tusClient.Upload(file, options);
            if (!resume) {
                upload.start();
                return Promise.resolve(upload);
            }
            return upload.findPreviousUploads().then(function(previous) {
                if (previous.length) {
                    upload.resumeFromPreviousUpload(previous[0]);
                    resumed = true;
                }
                upload.start();
                return upload;
            });
        }

        return Promise.resolve().then(function() {
            var validation = validateFile(file, settings.config);
            if (validation) {
                throw validation;
            }
            if (settings.videoId) {
                return settings.request('upload_credentials', {video_id: settings.videoId});
            }
            return create();
        }).then(function(credentials) {
            return launch(credentials, Boolean(settings.videoId));
        }).catch(function(error) {
            // Only local validation failures reuse their own message; raw
            // transport errors (which may include signed URLs) are replaced.
            throw report(error, error.code === 'unsupported_extension' ||
                error.code === 'file_too_large' || error.code === 'unsupported_type' ? error : null);
        });
    }

    return {
        validateFile: validateFile,
        buildTusOptions: buildTusOptions,
        startUpload: startUpload
    };
}));
