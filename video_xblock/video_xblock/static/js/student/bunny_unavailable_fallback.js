// impl: FR-001-10
'use strict';

function showUnavailable(container, messageText) {
    var wrapper = container.ownerDocument.createElement('div');
    wrapper.className = 'bunny-video-unavailable';
    wrapper.textContent = messageText;
    container.innerHTML = '';
    container.appendChild(wrapper);
}

function initEmbedErrorFallback(options) {
    var container = options.container;
    var messageText = options.messageText;
    var playerjsApi = options.playerjsApi;
    if (!container) {
        return;
    }
    var iframe = container.querySelector('iframe');
    if (!iframe) {
        return;
    }
    var Player = playerjsApi && playerjsApi.Player;
    if (typeof Player !== 'function') {
        return;
    }
    if (typeof container.getAttribute === 'function' && container.getAttribute('data-bunny-unavailable-fallback') === '1') {
        return;
    }
    if (typeof container.setAttribute === 'function') {
        container.setAttribute('data-bunny-unavailable-fallback', '1');
    }
    var player = new Player(iframe);
    if (player && typeof player.on === 'function') {
        player.on('error', function () {
            showUnavailable(container, messageText);
        });
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        showUnavailable: showUnavailable,
        initEmbedErrorFallback: initEmbedErrorFallback,
    };
} else if (typeof window !== 'undefined') {
    window.bunnyUnavailableFallback = {
        showUnavailable: showUnavailable,
        initEmbedErrorFallback: initEmbedErrorFallback,
    };

    if (typeof document !== 'undefined') {
        var container = document.querySelector('.bunny-video-player');
        if (container) {
            initEmbedErrorFallback({
                container: container,
                messageText: 'Відео недоступне',
                playerjsApi: (window.playerjs || {}),
            });
        }
    }
}
