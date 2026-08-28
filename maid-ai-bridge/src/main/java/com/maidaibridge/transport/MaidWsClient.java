package com.maidaibridge.transport;

import com.google.gson.JsonObject;
import com.maidaibridge.MaidAiBridgeMod;
import com.maidaibridge.protocol.ProtocolCodec;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.Queue;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

public final class MaidWsClient implements WebSocket.Listener, AutoCloseable {
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    private final Queue<String> inbound = new ConcurrentLinkedQueue<>();
    private final Queue<String> outbound = new ConcurrentLinkedQueue<>();
    private final StringBuilder textBuffer = new StringBuilder();
    private final AtomicBoolean connecting = new AtomicBoolean(false);
    private volatile WebSocket socket;
    private volatile boolean closing;
    private volatile long lastConnectAttemptMs;
    private volatile long lastInboundMs;
    private volatile long lastPingMs;
    private volatile boolean justConnected;
    private URI uri;

    public MaidWsClient(String url) { setUrl(url); }

    public void setUrl(String url) {
        URI candidate = URI.create(url);
        String host = candidate.getHost();
        if (host == null || !(host.equals("127.0.0.1") || host.equals("localhost") || host.equals("::1"))) {
            throw new IllegalArgumentException("Maid AI Bridge only permits localhost WebSocket endpoints");
        }
        this.uri = candidate;
    }

    public boolean isConnected() { return socket != null && !socket.isOutputClosed(); }
    public boolean consumeJustConnected() { boolean value = justConnected; justConnected = false; return value; }
    public long lastInboundMs() { return lastInboundMs; }

    public void tick(Consumer<JsonObject> serverThreadHandler) {
        long now = System.currentTimeMillis();
        if (!closing && !isConnected() && !connecting.get() && now - lastConnectAttemptMs >= 3000) connect();
        String raw;
        while ((raw = inbound.poll()) != null) {
            try { serverThreadHandler.accept(ProtocolCodec.parse(raw)); }
            catch (RuntimeException ex) { MaidAiBridgeMod.LOGGER.warn("Rejected invalid Agent message: {}", ex.getMessage()); }
        }
        WebSocket current = socket;
        if (current != null) {
            while ((raw = outbound.poll()) != null) current.sendText(raw, true);
            if (now - lastPingMs >= 5000) {
                current.sendPing(ByteBuffer.wrap(new byte[]{1}));
                lastPingMs = now;
            }
            if (lastInboundMs > 0 && now - lastInboundMs > 15000) {
                MaidAiBridgeMod.LOGGER.warn("Agent heartbeat timed out; closing bridge socket");
                current.sendClose(1001, "heartbeat timeout");
                socket = null;
            }
        }
    }

    public void send(JsonObject envelope) { outbound.add(ProtocolCodec.encode(envelope)); }

    private void connect() {
        lastConnectAttemptMs = System.currentTimeMillis();
        if (!connecting.compareAndSet(false, true)) return;
        client.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(5)).buildAsync(uri, this)
                .whenComplete((ws, error) -> {
                    connecting.set(false);
                    if (error != null) {
                        MaidAiBridgeMod.LOGGER.debug("Agent Core not available at {}: {}", uri, error.toString());
                    } else {
                        socket = ws;
                        lastInboundMs = System.currentTimeMillis();
                        justConnected = true;
                        MaidAiBridgeMod.LOGGER.info("Connected to Agent Core at {}", uri);
                    }
                });
    }

    @Override public void onOpen(WebSocket webSocket) { WebSocket.Listener.super.onOpen(webSocket); webSocket.request(1); }
    @Override public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
        synchronized (textBuffer) {
            textBuffer.append(data);
            if (last) { inbound.add(textBuffer.toString()); textBuffer.setLength(0); }
        }
        lastInboundMs = System.currentTimeMillis(); webSocket.request(1); return null;
    }
    @Override public CompletionStage<?> onPong(WebSocket webSocket, ByteBuffer message) {
        lastInboundMs = System.currentTimeMillis(); webSocket.request(1); return null;
    }
    @Override public CompletionStage<?> onPing(WebSocket webSocket, ByteBuffer message) {
        lastInboundMs = System.currentTimeMillis(); webSocket.sendPong(message); webSocket.request(1); return null;
    }
    @Override public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
        if (socket == webSocket) socket = null;
        MaidAiBridgeMod.LOGGER.info("Agent socket closed: {} {}", statusCode, reason); return null;
    }
    @Override public void onError(WebSocket webSocket, Throwable error) {
        if (socket == webSocket) socket = null;
        MaidAiBridgeMod.LOGGER.warn("Agent socket error: {}", error.toString());
    }
    @Override public void close() {
        closing = true; WebSocket current = socket;
        if (current != null) current.sendClose(WebSocket.NORMAL_CLOSURE, "server stopping");
        socket = null; inbound.clear(); outbound.clear();
    }
}
