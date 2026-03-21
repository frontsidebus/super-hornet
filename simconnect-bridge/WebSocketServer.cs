using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Serialization;
using Fleck;
using SimConnectBridge.Models;

namespace SimConnectBridge;

/// <summary>
/// Fleck-based WebSocket server that broadcasts sim state to connected clients
/// and handles incoming request messages.
///
/// Improvements:
///  - Structured logging with timestamps and severity.
///  - Clean handling of client disconnects (no unhandled exceptions).
///  - Heartbeat support: responds to "heartbeat" messages from clients.
///  - Tracks per-client message counts for diagnostics.
/// </summary>
public sealed class TelemetryWebSocketServer : IDisposable
{
    private readonly WebSocketServer _server;
    private readonly ConcurrentDictionary<Guid, ClientConnection> _clients = new();
    private readonly JsonSerializerOptions _jsonOptions;
    private bool _disposed;

    /// <summary>
    /// Creates the WebSocket server bound to the given host and port.
    /// </summary>
    /// <param name="host">Bind address (e.g., "0.0.0.0").</param>
    /// <param name="port">Port number (e.g., 8080).</param>
    public TelemetryWebSocketServer(string host, int port)
    {
        _server = new WebSocketServer($"ws://{host}:{port}");

        _jsonOptions = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            WriteIndented = false
        };
    }

    /// <summary>
    /// Number of currently connected clients.
    /// </summary>
    public int ClientCount => _clients.Count;

    /// <summary>
    /// Starts listening for WebSocket connections.
    /// </summary>
    public void Start()
    {
        _server.Start(socket =>
        {
            socket.OnOpen = () => OnClientOpen(socket);
            socket.OnClose = () => OnClientClose(socket);
            socket.OnMessage = message => OnClientMessage(socket, message);
            socket.OnError = ex => OnClientError(socket, ex);
        });

        Log("INFO", $"Server started on {_server.Location}");
    }

    /// <summary>
    /// Broadcasts the current sim state to all connected clients.
    /// Respects per-client field subscriptions.
    /// </summary>
    /// <param name="state">The current simulation state.</param>
    public void BroadcastState(SimState state)
    {
        if (_clients.IsEmpty) return;

        // Pre-serialize the full state once for clients with no filter
        string? fullJson = null;

        foreach (var (_, client) in _clients)
        {
            try
            {
                // Skip clients that are no longer available
                if (!client.Socket.IsAvailable)
                {
                    RemoveClient(client);
                    continue;
                }

                string json;
                if (client.SubscribedFields is null || client.SubscribedFields.Count == 0)
                {
                    fullJson ??= JsonSerializer.Serialize(state, _jsonOptions);
                    json = fullJson;
                }
                else
                {
                    json = SerializeFilteredState(state, client.SubscribedFields);
                }

                client.Socket.Send(json);
                client.MessagesSent++;
            }
            catch (Exception ex)
            {
                Log("WARN", $"Error sending to client {client.Id}: {ex.Message}");
                RemoveClient(client);
            }
        }
    }

    /// <summary>
    /// Stops the WebSocket server and disconnects all clients.
    /// </summary>
    public void Stop()
    {
        foreach (var (_, client) in _clients)
        {
            try { client.Socket.Close(); }
            catch { /* best-effort */ }
        }
        _clients.Clear();
        _server.Dispose();
        Log("INFO", "Server stopped.");
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        Stop();
    }

    // -----------------------------------------------------------------------
    //  Structured logging
    // -----------------------------------------------------------------------

    private static void Log(string level, string message)
    {
        var ts = DateTimeOffset.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ");
        Console.WriteLine($"{ts} [{level}] WebSocket: {message}");
    }

    // -----------------------------------------------------------------------
    //  Connection handlers
    // -----------------------------------------------------------------------

    private void OnClientOpen(IWebSocketConnection socket)
    {
        var client = new ClientConnection(socket);
        _clients[client.Id] = client;
        Log("INFO",
            $"Client connected: {client.Id} " +
            $"({socket.ConnectionInfo.ClientIpAddress}) " +
            $"[total: {_clients.Count}]");
    }

    private void OnClientClose(IWebSocketConnection socket)
    {
        var id = GetClientId(socket);
        if (id is not null && _clients.TryRemove(id.Value, out var client))
        {
            Log("INFO",
                $"Client disconnected: {id} " +
                $"(sent {client.MessagesSent} msgs) " +
                $"[remaining: {_clients.Count}]");
        }
    }

    private void OnClientMessage(IWebSocketConnection socket, string message)
    {
        try
        {
            var request = JsonSerializer.Deserialize<ClientRequest>(message, _jsonOptions);
            if (request is null) return;

            var clientId = GetClientId(socket);
            if (clientId is null) return;

            switch (request.Type)
            {
                case "get_state":
                    HandleGetState(socket);
                    break;

                case "subscribe":
                    HandleSubscribe(clientId.Value, request.Fields);
                    break;

                case "heartbeat":
                    HandleHeartbeat(socket);
                    break;

                default:
                    Log("DEBUG", $"Unknown request type from {clientId}: {request.Type}");
                    var errorResponse = JsonSerializer.Serialize(new
                    {
                        type = "error",
                        message = $"Unknown request type: {request.Type}"
                    }, _jsonOptions);
                    SafeSend(socket, errorResponse);
                    break;
            }
        }
        catch (JsonException ex)
        {
            Log("WARN", $"Invalid JSON from client: {ex.Message}");
            var errorResponse = JsonSerializer.Serialize(new
            {
                type = "error",
                message = "Invalid JSON"
            }, _jsonOptions);
            SafeSend(socket, errorResponse);
        }
    }

    private void OnClientError(IWebSocketConnection socket, Exception ex)
    {
        var clientId = GetClientId(socket);

        // ObjectDisposedException and IOException are normal during
        // client disconnect -- log at DEBUG level to avoid noise.
        if (ex is ObjectDisposedException or System.IO.IOException)
        {
            Log("DEBUG", $"Client {clientId} socket error (expected on disconnect): {ex.GetType().Name}");
        }
        else
        {
            Log("WARN", $"Client {clientId} error: {ex.Message}");
        }

        if (clientId is not null)
        {
            RemoveClient(clientId.Value);
        }
    }

    // -----------------------------------------------------------------------
    //  Request handlers
    // -----------------------------------------------------------------------

    private void HandleGetState(IWebSocketConnection socket)
    {
        var response = new
        {
            type = "state_response",
            message = "Full state will be delivered on next update cycle."
        };
        SafeSend(socket, JsonSerializer.Serialize(response, _jsonOptions));
    }

    private void HandleSubscribe(Guid clientId, List<string>? fields)
    {
        if (_clients.TryGetValue(clientId, out var client))
        {
            client.SubscribedFields = fields;
            Log("INFO",
                $"Client {clientId} subscribed to: " +
                $"{(fields is null ? "all" : string.Join(", ", fields))}");

            var ack = new
            {
                type = "subscribe_ack",
                fields = fields ?? new List<string> { "all" }
            };
            SafeSend(client.Socket, JsonSerializer.Serialize(ack, _jsonOptions));
        }
    }

    private void HandleHeartbeat(IWebSocketConnection socket)
    {
        var response = new
        {
            type = "heartbeat_ack",
            timestamp = DateTimeOffset.UtcNow.ToString("o"),
            clients = _clients.Count
        };
        SafeSend(socket, JsonSerializer.Serialize(response, _jsonOptions));
    }

    // -----------------------------------------------------------------------
    //  Helpers
    // -----------------------------------------------------------------------

    /// <summary>
    /// Safely send a message, catching any exception from a disconnected socket.
    /// </summary>
    private void SafeSend(IWebSocketConnection socket, string message)
    {
        try
        {
            if (socket.IsAvailable)
            {
                socket.Send(message);
            }
        }
        catch (Exception ex)
        {
            Log("DEBUG", $"SafeSend failed: {ex.Message}");
        }
    }

    private Guid? GetClientId(IWebSocketConnection socket)
    {
        foreach (var (id, client) in _clients)
        {
            if (client.Socket == socket) return id;
        }
        return null;
    }

    private void RemoveClient(ClientConnection client)
    {
        RemoveClient(client.Id);
    }

    private void RemoveClient(Guid clientId)
    {
        if (_clients.TryRemove(clientId, out _))
        {
            Log("DEBUG", $"Removed stale client {clientId} [remaining: {_clients.Count}]");
        }
    }

    /// <summary>
    /// Serializes only the requested top-level fields of the sim state.
    /// </summary>
    private string SerializeFilteredState(SimState state, List<string> fields)
    {
        var dict = new Dictionary<string, object?>
        {
            ["timestamp"] = state.Timestamp,
            ["connected"] = state.Connected
        };

        foreach (var field in fields)
        {
            switch (field.ToLowerInvariant())
            {
                case "position": dict["position"] = state.Position; break;
                case "attitude": dict["attitude"] = state.Attitude; break;
                case "speeds": dict["speeds"] = state.Speeds; break;
                case "engines": dict["engines"] = state.Engines; break;
                case "autopilot": dict["autopilot"] = state.Autopilot; break;
                case "radios": dict["radios"] = state.Radios; break;
                case "fuel": dict["fuel"] = state.Fuel; break;
                case "surfaces": dict["surfaces"] = state.Surfaces; break;
                case "environment": dict["environment"] = state.Environment; break;
                case "aircraft": dict["aircraft"] = state.Aircraft; break;
            }
        }

        return JsonSerializer.Serialize(dict, _jsonOptions);
    }

    // -----------------------------------------------------------------------
    //  Inner types
    // -----------------------------------------------------------------------

    /// <summary>
    /// Tracks a single WebSocket client connection and its subscription preferences.
    /// </summary>
    private sealed class ClientConnection
    {
        public Guid Id { get; } = Guid.NewGuid();
        public IWebSocketConnection Socket { get; }
        public List<string>? SubscribedFields { get; set; }
        public long MessagesSent { get; set; }

        public ClientConnection(IWebSocketConnection socket)
        {
            Socket = socket;
        }
    }

    /// <summary>
    /// Deserialized client request message.
    /// </summary>
    private sealed class ClientRequest
    {
        [JsonPropertyName("type")]
        public string Type { get; set; } = string.Empty;

        [JsonPropertyName("fields")]
        public List<string>? Fields { get; set; }
    }
}
