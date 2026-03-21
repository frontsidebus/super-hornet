using System.Text.Json;
using SimConnectBridge;

// ---------------------------------------------------------------------------
//  MERLIN SimConnect Bridge -- Entry Point
//  Connects to MSFS 2024 via SimConnect and broadcasts telemetry over WebSocket.
//
//  The bridge automatically reconnects when MSFS is restarted -- no manual
//  restart required.  If SimConnect is not registered (COM error 0xe0434352),
//  it logs a diagnostic message and retries.
// ---------------------------------------------------------------------------

Log("INFO", "=== MERLIN SimConnect Bridge ===");

// Load configuration
var config = LoadConfiguration();

string appName = config.GetProperty("SimConnect").GetProperty("AppName").GetString()
    ?? "MERLIN SimConnect Bridge";
int highHz = config.GetProperty("SimConnect").GetProperty("HighFrequencyHz").GetInt32();
int lowHz = config.GetProperty("SimConnect").GetProperty("LowFrequencyHz").GetInt32();
string wsHost = config.GetProperty("WebSocket").GetProperty("Host").GetString() ?? "0.0.0.0";
int wsPort = config.GetProperty("WebSocket").GetProperty("Port").GetInt32();

Log("INFO", $"Config: HF={highHz}Hz, LF={lowHz}Hz, WS={wsHost}:{wsPort}");

// Set up cancellation for graceful shutdown
using var cts = new CancellationTokenSource();

Console.CancelKeyPress += (_, e) =>
{
    e.Cancel = true;
    Log("INFO", "Shutdown requested...");
    cts.Cancel();
};

AppDomain.CurrentDomain.ProcessExit += (_, _) =>
{
    cts.Cancel();
};

// Start WebSocket server
using var wsServer = new TelemetryWebSocketServer(wsHost, wsPort);
wsServer.Start();

// Start SimConnect manager
using var simConnect = new SimConnectManager(appName, highHz, lowHz);

// Wire up state updates to WebSocket broadcast
simConnect.StateUpdated += state => wsServer.BroadcastState(state);

simConnect.ConnectionChanged += connected =>
{
    Log("INFO", $"SimConnect connected: {connected}");
};

simConnect.FlightStateChanged += active =>
{
    Log("INFO", active
        ? "Flight is active — telemetry streaming"
        : "Flight ended — idling (telemetry paused)");
};

// Connect with retry -- the ConnectWithRetryAsync loop now handles
// MSFS crashes/restarts automatically by re-entering the retry loop
// when the connection is lost.
try
{
    Log("INFO", $"Attempting SimConnect connection as \"{appName}\"...");
    await simConnect.ConnectWithRetryAsync(cts.Token);
}
catch (OperationCanceledException)
{
    // Expected on shutdown
}
catch (Exception ex)
{
    Log("ERROR", $"Fatal error: {ex.Message}");
    Log("ERROR", ex.StackTrace ?? "(no stack trace)");
}

Log("INFO", "Shutting down...");
simConnect.Disconnect();
wsServer.Stop();
Log("INFO", "Goodbye.");

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------

static void Log(string level, string message)
{
    var ts = DateTimeOffset.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ");
    Console.WriteLine($"{ts} [{level}] Bridge: {message}");
}

static JsonElement LoadConfiguration()
{
    const string configPath = "appsettings.json";

    if (!File.Exists(configPath))
    {
        Log("WARN", $"{configPath} not found. Using defaults.");
        var defaults = """
        {
          "SimConnect": {
            "AppName": "MERLIN SimConnect Bridge",
            "HighFrequencyHz": 30,
            "LowFrequencyHz": 1
          },
          "WebSocket": {
            "Port": 8080,
            "Host": "0.0.0.0"
          }
        }
        """;
        return JsonDocument.Parse(defaults).RootElement;
    }

    var json = File.ReadAllText(configPath);
    return JsonDocument.Parse(json).RootElement;
}
