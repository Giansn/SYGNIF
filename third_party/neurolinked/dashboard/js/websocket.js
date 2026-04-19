/**
 * BrainWebSocket - Manages WebSocket connection to the NeuroLinked brain server.
 */
export class BrainWebSocket {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 10000;
        this.onInit = null;    // callback(positions)
        this.onState = null;   // callback(state)
        this.onDisconnect = null;
    }

    connect() {
        try {
            this.ws = new WebSocket(this.url);
        } catch (e) {
            console.error('[WS] Failed to create WebSocket:', e);
            this._scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('[WS] Connected');
            this.reconnectDelay = 1000;
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'init' && this.onInit) {
                    this.onInit(msg.positions);
                } else if (msg.type === 'state' && this.onState) {
                    this.onState(msg.data);
                }
            } catch (e) {
                console.error('[WS] Parse error:', e);
            }
        };

        this.ws.onclose = () => {
            console.log('[WS] Disconnected');
            if (this.onDisconnect) this.onDisconnect();
            this._scheduleReconnect();
        };

        this.ws.onerror = (e) => {
            console.error('[WS] Error:', e);
        };
    }

    _scheduleReconnect() {
        setTimeout(() => {
            console.log('[WS] Reconnecting...');
            this.connect();
        }, this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, this.maxReconnectDelay);
    }

    sendTextInput(text) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'text_input', text }));
        }
    }

    sendCommand(cmd) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'command', cmd }));
        }
    }
}
