using System.Runtime.InteropServices;
using Microsoft.FlightSimulator.SimConnect;
using SimConnectBridge.Models;

namespace SimConnectBridge;

/// <summary>
/// Manages the SimConnect connection lifecycle, data definition registration,
/// periodic polling, and state updates.
///
/// Supports automatic reconnection when MSFS crashes or is restarted -- the
/// manager detects sustained COM errors or a OnRecvQuit callback and re-enters
/// the retry loop without requiring a process restart.
/// </summary>
public sealed class SimConnectManager : IDisposable
{
    private SimConnect? _simConnect;
    private readonly string _appName;
    private readonly int _highFrequencyHz;
    private readonly int _lowFrequencyHz;
    private readonly object _lock = new();

    private Timer? _highFreqTimer;
    private Timer? _lowFreqTimer;
    private Thread? _messagePumpThread;
    private EventWaitHandle? _simConnectEvent;
    private bool _connected;
    private bool _disposed;
    private bool _autoReconnect = true;
    private CancellationTokenSource? _reconnectCts;
    private volatile bool _pumpRunning;
    private bool _flightActive;
    private bool _dataSubscribed;

    /// <summary>
    /// The current simulation state, updated on each data receive callback.
    /// </summary>
    public SimState CurrentState { get; } = new();

    /// <summary>
    /// Raised whenever the sim state is updated with new telemetry data.
    /// </summary>
    public event Action<SimState>? StateUpdated;

    /// <summary>
    /// Raised when the SimConnect connection status changes.
    /// </summary>
    public event Action<bool>? ConnectionChanged;

    /// <summary>
    /// Raised when the flight active state changes (flight loaded/ended).
    /// </summary>
    public event Action<bool>? FlightStateChanged;

    /// <summary>
    /// Creates a new <see cref="SimConnectManager"/> with the given configuration.
    /// </summary>
    /// <param name="appName">Application name registered with SimConnect.</param>
    /// <param name="highFrequencyHz">Poll rate for position/attitude/speed data.</param>
    /// <param name="lowFrequencyHz">Poll rate for fuel/environment/autopilot data.</param>
    public SimConnectManager(string appName, int highFrequencyHz = 30, int lowFrequencyHz = 1)
    {
        _appName = appName;
        _highFrequencyHz = highFrequencyHz;
        _lowFrequencyHz = lowFrequencyHz;
    }

    /// <summary>
    /// Attempts to open a connection to MSFS via SimConnect.
    /// Starts polling timers on success.
    /// </summary>
    /// <returns>True if the connection was established; false otherwise.</returns>
    public bool Connect()
    {
        try
        {
            // Use an event handle for the message pump instead of a window handle.
            // This is required for console apps (no Win32 message loop). SimConnect
            // signals this event when data is available, and our pump thread calls
            // ReceiveMessage() in response.
            _simConnectEvent = new EventWaitHandle(false, EventResetMode.AutoReset);
            _simConnect = new SimConnect(_appName, IntPtr.Zero, 0, _simConnectEvent, 0);

            _simConnect.OnRecvOpen += OnRecvOpen;
            _simConnect.OnRecvQuit += OnRecvQuit;
            _simConnect.OnRecvException += OnRecvException;
            _simConnect.OnRecvSimobjectData += OnRecvSimobjectData;
            _simConnect.OnRecvEvent += OnRecvEvent;

            RegisterDataDefinitions();
            SubscribeSystemEvents();

            // Start a dedicated thread that waits on the event handle and pumps
            // messages. This replaces the timer-based approach which raced with
            // the SimConnect event model and caused 0x80004005 errors.
            _pumpRunning = true;
            _messagePumpThread = new Thread(MessagePumpLoop)
            {
                Name = "SimConnect-MessagePump",
                IsBackground = true,
            };
            _messagePumpThread.Start();

            _connected = true;
            CurrentState.Connected = true;
            ConnectionChanged?.Invoke(true);

            Log("INFO", "Connection opened");
            return true;
        }
        catch (COMException ex)
        {
            // 0xe0434352 is the generic CLR exception HResult -- this happens
            // when SimConnect.dll is not registered (MSI not re-run after
            // MSFS restart).  We log a helpful message rather than crashing.
            if (ex.HResult == unchecked((int)0xe0434352) || ex.HResult == unchecked((int)0x80004005))
            {
                Log("ERROR",
                    $"SimConnect COM error 0x{ex.HResult:X8}. " +
                    "SimConnect SDK may need re-registration. " +
                    "Try re-running the SimConnect MSI installer from the MSFS SDK folder.");
            }
            else
            {
                Log("WARN", $"Failed to connect: 0x{ex.HResult:X8} -- {ex.Message}");
            }
            _connected = false;
            CurrentState.Connected = false;
            return false;
        }
        catch (Exception ex)
        {
            Log("ERROR", $"Unexpected error during connect: {ex.Message}");
            _connected = false;
            CurrentState.Connected = false;
            return false;
        }
    }

    /// <summary>
    /// Attempts to connect to SimConnect in a retry loop until cancelled.
    /// On disconnect, automatically re-enters the retry loop if
    /// <paramref name="cancellationToken"/> has not been cancelled.
    /// </summary>
    /// <param name="cancellationToken">Token to cancel the retry loop.</param>
    /// <param name="retryDelayMs">Delay between connection attempts.</param>
    public async Task ConnectWithRetryAsync(
        CancellationToken cancellationToken, int retryDelayMs = 5000)
    {
        _autoReconnect = true;
        _reconnectCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);

        while (!_reconnectCts.Token.IsCancellationRequested)
        {
            // --- Connection attempt loop ---
            while (!_reconnectCts.Token.IsCancellationRequested)
            {
                if (Connect())
                    break;

                Log("INFO", $"Retrying in {retryDelayMs}ms...");
                await Task.Delay(retryDelayMs, _reconnectCts.Token)
                    .ConfigureAwait(false);
            }

            if (_reconnectCts.Token.IsCancellationRequested)
                break;

            // --- Wait for disconnect ---
            // We spin here until HandleDisconnect sets _connected = false,
            // which means MSFS quit or a sustained COM error occurred.
            while (_connected && !_reconnectCts.Token.IsCancellationRequested)
            {
                await Task.Delay(1000, _reconnectCts.Token)
                    .ConfigureAwait(false);
            }

            if (_reconnectCts.Token.IsCancellationRequested || !_autoReconnect)
                break;

            Log("INFO", "SimConnect lost. Will attempt auto-reconnect...");
            // Brief pause before retrying to avoid tight-loop on fast
            // repeated disconnects.
            await Task.Delay(retryDelayMs, _reconnectCts.Token)
                .ConfigureAwait(false);
        }
    }

    /// <summary>
    /// Disconnects from SimConnect and stops all polling timers.
    /// </summary>
    public void Disconnect()
    {
        _autoReconnect = false;
        _reconnectCts?.Cancel();
        StopTimers();

        if (_simConnect is not null)
        {
            try
            {
                _simConnect.Dispose();
            }
            catch (Exception ex)
            {
                Log("WARN", $"Error during disconnect: {ex.Message}");
            }
            _simConnect = null;
        }

        _connected = false;
        CurrentState.Connected = false;
        ConnectionChanged?.Invoke(false);
        Log("INFO", "Disconnected.");
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        Disconnect();
    }

    // -----------------------------------------------------------------------
    //  Structured logging helper
    // -----------------------------------------------------------------------

    private static void Log(string level, string message)
    {
        var ts = DateTimeOffset.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ");
        Console.WriteLine($"{ts} [{level}] SimConnect: {message}");
    }

    // -----------------------------------------------------------------------
    //  Data Definition Registration
    // -----------------------------------------------------------------------

    private void RegisterDataDefinitions()
    {
        if (_simConnect is null) return;

        int sendCount = 0;

        void RegFloat64(DataDefinitionId defId, string varName, string units)
        {
            Log("DEBUG", $"  [{sendCount++}] {defId}: {varName} ({units})");
            AddFloat64(_simConnect!, defId, varName, units);
        }

        void RegInt32(DataDefinitionId defId, string varName, string units)
        {
            Log("DEBUG", $"  [{sendCount++}] {defId}: {varName} ({units})");
            AddInt32(_simConnect!, defId, varName, units);
        }

        Log("INFO", "Registering data definitions...");

        // -- High-frequency data (position, attitude, speeds) --
        var hf = DataDefinitionId.HighFrequency;
        RegFloat64(hf, "PLANE LATITUDE", "degrees");
        RegFloat64(hf, "PLANE LONGITUDE", "degrees");
        RegFloat64(hf, "PLANE ALTITUDE", "feet");
        RegFloat64(hf, "PLANE ALT ABOVE GROUND", "feet");
        RegFloat64(hf, "PLANE PITCH DEGREES", "degrees");
        RegFloat64(hf, "PLANE BANK DEGREES", "degrees");
        RegFloat64(hf, "PLANE HEADING DEGREES TRUE", "degrees");
        RegFloat64(hf, "PLANE HEADING DEGREES MAGNETIC", "degrees");
        RegFloat64(hf, "AIRSPEED INDICATED", "knots");
        RegFloat64(hf, "AIRSPEED TRUE", "knots");
        RegFloat64(hf, "GROUND VELOCITY", "knots");
        RegFloat64(hf, "AIRSPEED MACH", "mach");
        RegFloat64(hf, "VERTICAL SPEED", "feet per minute");

        // -- Low-frequency data (autopilot, radios, fuel, surfaces, environment) --
        var lf = DataDefinitionId.LowFrequency;
        RegInt32(lf, "AUTOPILOT MASTER", "bool");
        RegFloat64(lf, "AUTOPILOT HEADING LOCK DIR", "degrees");
        RegFloat64(lf, "AUTOPILOT ALTITUDE LOCK VAR", "feet");
        RegFloat64(lf, "AUTOPILOT VERTICAL HOLD VAR", "feet per minute");
        RegFloat64(lf, "AUTOPILOT AIRSPEED HOLD VAR", "knots");
        RegFloat64(lf, "COM ACTIVE FREQUENCY:1", "MHz");
        RegFloat64(lf, "COM ACTIVE FREQUENCY:2", "MHz");
        RegFloat64(lf, "NAV ACTIVE FREQUENCY:1", "MHz");
        RegFloat64(lf, "NAV ACTIVE FREQUENCY:2", "MHz");
        RegFloat64(lf, "FUEL TOTAL QUANTITY", "gallons");
        RegFloat64(lf, "FUEL TOTAL QUANTITY WEIGHT", "pounds");
        RegInt32(lf, "GEAR HANDLE POSITION", "bool");
        RegFloat64(lf, "FLAPS HANDLE PERCENT", "percent");
        RegFloat64(lf, "SPOILERS HANDLE POSITION", "percent");
        RegFloat64(lf, "AMBIENT WIND VELOCITY", "knots");
        RegFloat64(lf, "AMBIENT WIND DIRECTION", "degrees");
        RegFloat64(lf, "AMBIENT VISIBILITY", "meters");
        RegFloat64(lf, "AMBIENT TEMPERATURE", "celsius");
        RegFloat64(lf, "KOHLSMAN SETTING HG", "inHg");

        // -- Engine data (4 engines x 6 params) --
        var eng = DataDefinitionId.EngineData;
        for (int i = 1; i <= 4; i++)
        {
            RegFloat64(eng, $"GENERAL ENG RPM:{i}", "rpm");
            RegFloat64(eng, $"ENG MANIFOLD PRESSURE:{i}", "inHg");
            RegFloat64(eng, $"ENG FUEL FLOW GPH:{i}", "gallons per hour");
            RegFloat64(eng, $"ENG EXHAUST GAS TEMPERATURE:{i}", "rankine");
            RegFloat64(eng, $"ENG OIL TEMPERATURE:{i}", "rankine");
            RegFloat64(eng, $"ENG OIL PRESSURE:{i}", "psf");
        }

        // -- Aircraft title (string) --
        Log("DEBUG", $"  [{sendCount++}] AircraftTitle: TITLE (string256)");
        _simConnect.AddToDataDefinition(
            DataDefinitionId.AircraftTitle,
            "TITLE",
            null,
            SIMCONNECT_DATATYPE.STRING256,
            0.0f,
            SimConnect.SIMCONNECT_UNUSED);

        // Register struct mappings
        _simConnect.RegisterDataDefineStruct<HighFrequencyData>(DataDefinitionId.HighFrequency);
        _simConnect.RegisterDataDefineStruct<LowFrequencyData>(DataDefinitionId.LowFrequency);
        _simConnect.RegisterDataDefineStruct<EngineDataStruct>(DataDefinitionId.EngineData);
        _simConnect.RegisterDataDefineStruct<AircraftTitleData>(DataDefinitionId.AircraftTitle);

        Log("INFO", $"{sendCount} data definitions registered.");
    }

    private static void AddFloat64(SimConnect sc, DataDefinitionId defId, string varName, string units)
    {
        sc.AddToDataDefinition(defId, varName, units,
            SIMCONNECT_DATATYPE.FLOAT64, 0.0f, SimConnect.SIMCONNECT_UNUSED);
    }

    private static void AddInt32(SimConnect sc, DataDefinitionId defId, string varName, string units)
    {
        sc.AddToDataDefinition(defId, varName, units,
            SIMCONNECT_DATATYPE.INT32, 0.0f, SimConnect.SIMCONNECT_UNUSED);
    }

    // -----------------------------------------------------------------------
    //  Polling Timers
    // -----------------------------------------------------------------------

    private void StartDataSubscriptions()
    {
        if (_dataSubscribed) return;
        _dataSubscribed = true;

        // Use subscription-based data delivery instead of timer-based polling.
        // This tells SimConnect to push data automatically, which properly
        // signals the event handle for the message pump thread.
        try
        {
            // High-frequency: position/attitude/speeds — every sim frame
            _simConnect?.RequestDataOnSimObject(
                DataRequestId.HighFrequency,
                DataDefinitionId.HighFrequency,
                SimConnect.SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_PERIOD.SIM_FRAME,
                SIMCONNECT_DATA_REQUEST_FLAG.CHANGED,
                0, 0, 0);

            // Low-frequency: autopilot/radios/fuel/surfaces/environment — every second
            _simConnect?.RequestDataOnSimObject(
                DataRequestId.LowFrequency,
                DataDefinitionId.LowFrequency,
                SimConnect.SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_PERIOD.SECOND,
                SIMCONNECT_DATA_REQUEST_FLAG.CHANGED,
                0, 0, 0);

            // Engine data — every second
            _simConnect?.RequestDataOnSimObject(
                DataRequestId.EngineData,
                DataDefinitionId.EngineData,
                SimConnect.SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_PERIOD.SECOND,
                SIMCONNECT_DATA_REQUEST_FLAG.CHANGED,
                0, 0, 0);

            // Aircraft title — once (doesn't change mid-flight)
            _simConnect?.RequestDataOnSimObject(
                DataRequestId.AircraftTitle,
                DataDefinitionId.AircraftTitle,
                SimConnect.SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_PERIOD.ONCE,
                SIMCONNECT_DATA_REQUEST_FLAG.DEFAULT,
                0, 0, 0);

            Log("INFO", "Data subscriptions registered (HF=SIM_FRAME, LF=SECOND)");
        }
        catch (COMException ex)
        {
            Log("WARN", $"Failed to register data subscriptions: 0x{ex.HResult:X8}");
        }
    }

    private void StopTimers()
    {
        _highFreqTimer?.Dispose();
        _highFreqTimer = null;
        _lowFreqTimer?.Dispose();
        _lowFreqTimer = null;

        // Stop the event-driven message pump
        _pumpRunning = false;
        _simConnectEvent?.Set(); // unblock the WaitOne
        _messagePumpThread?.Join(2000);
        _messagePumpThread = null;
        _simConnectEvent?.Dispose();
        _simConnectEvent = null;
    }

    // -----------------------------------------------------------------------
    //  Message pump (event-driven, runs on dedicated thread)
    // -----------------------------------------------------------------------

    private int _consecutiveErrors;

    /// <summary>
    /// Event-driven message pump loop. Blocks on the SimConnect event handle
    /// and calls ReceiveMessage() when data is available. This replaces the
    /// old timer-based approach which caused 0x80004005 errors because it
    /// polled without synchronization.
    /// </summary>
    private void MessagePumpLoop()
    {
        Log("DEBUG", "Message pump thread started");
        while (_pumpRunning && _simConnectEvent != null)
        {
            try
            {
                // Block until SimConnect signals data is ready (or timeout)
                bool signaled = _simConnectEvent.WaitOne(100);
                if (!_pumpRunning) break;
                if (!signaled) continue;

                _simConnect?.ReceiveMessage();
                _consecutiveErrors = 0;
            }
            catch (COMException ex)
            {
                _consecutiveErrors++;
                if (_consecutiveErrors <= 3)
                {
                    Log("WARN", $"COM error in message pump: 0x{ex.HResult:X8} (attempt {_consecutiveErrors})");
                }
                else if (_consecutiveErrors > 200)
                {
                    // 200 errors at ~100ms each = ~20 seconds of sustained failure.
                    // This is a real disconnection, not just a slow sim load.
                    Log("ERROR",
                        $"Sustained COM errors ({_consecutiveErrors}), treating as disconnect. " +
                        $"HResult=0x{ex.HResult:X8}");
                    HandleDisconnect();
                    break;
                }
            }
            catch (Exception ex) when (!_pumpRunning)
            {
                // Expected during shutdown
                Log("DEBUG", $"Pump exiting: {ex.Message}");
                break;
            }
        }
        Log("DEBUG", "Message pump thread stopped");
    }

    // -----------------------------------------------------------------------
    //  SimConnect Callbacks
    // -----------------------------------------------------------------------

    private void SubscribeSystemEvents()
    {
        if (_simConnect is null) return;

        // Subscribe to sim lifecycle events so we only poll when a flight is active
        _simConnect.SubscribeToSystemEvent(SimEventId.FlightLoaded, "FlightLoaded");
        _simConnect.SubscribeToSystemEvent(SimEventId.SimStart, "SimStart");
        _simConnect.SubscribeToSystemEvent(SimEventId.SimStop, "SimStop");
        _simConnect.SubscribeToSystemEvent(SimEventId.Paused, "Pause");
        _simConnect.SubscribeToSystemEvent(SimEventId.Unpaused, "Unpaused");

        Log("INFO", "System event subscriptions registered");
    }

    private void OnRecvEvent(SimConnect sender, SIMCONNECT_RECV_EVENT data)
    {
        switch ((SimEventId)data.uEventID)
        {
            case SimEventId.FlightLoaded:
                Log("INFO", "Flight loaded — starting data subscriptions");
                _flightActive = true;
                FlightStateChanged?.Invoke(true);
                StartDataSubscriptions();
                break;

            case SimEventId.SimStart:
                Log("INFO", "Sim started");
                if (!_dataSubscribed)
                {
                    _flightActive = true;
                    FlightStateChanged?.Invoke(true);
                    StartDataSubscriptions();
                }
                break;

            case SimEventId.SimStop:
                Log("INFO", "Sim stopped — flight ended, idling");
                _flightActive = false;
                FlightStateChanged?.Invoke(false);
                break;

            case SimEventId.Paused:
                Log("DEBUG", "Sim paused");
                break;

            case SimEventId.Unpaused:
                Log("DEBUG", "Sim unpaused");
                break;
        }
    }

    private void OnRecvOpen(SimConnect sender, SIMCONNECT_RECV_OPEN data)
    {
        Log("INFO", $"Recv Open: {data.szApplicationName}");
        Log("INFO", "Connected to MSFS — waiting for flight to load...");
        _consecutiveErrors = 0;

        // Don't start data subscriptions yet — wait for FlightLoaded or SimStart.
        // If the user is already in a flight when the bridge starts, SimStart
        // fires shortly after Open. If on the main menu, we idle quietly.
        //
        // As a fallback, try after 5s in case the events don't fire (some
        // MSFS versions/states may not emit FlightLoaded on reconnect).
        var fallbackTimer = new Timer(_ =>
        {
            if (!_dataSubscribed && _connected)
            {
                Log("INFO", "No flight event received — trying data subscriptions anyway");
                StartDataSubscriptions();
            }
        }, null, 5000, Timeout.Infinite);
    }

    private void OnRecvQuit(SimConnect sender, SIMCONNECT_RECV data)
    {
        Log("WARN", "Simulator quit detected.");
        HandleDisconnect();
    }

    private void OnRecvException(SimConnect sender, SIMCONNECT_RECV_EXCEPTION data)
    {
        var ex = (SIMCONNECT_EXCEPTION)data.dwException;
        Log("WARN", $"Exception: {ex} (SendID={data.dwSendID}, Index={data.dwIndex})");

        // NAME_UNRECOGNIZED and other definition errors are non-fatal warnings.
        // Only treat connection-level errors as disconnects.
        if (ex == SIMCONNECT_EXCEPTION.ERROR)
        {
            HandleDisconnect();
        }
    }

    /// <summary>
    /// Handles incoming sim object data and updates the current state.
    /// </summary>
    private void OnRecvSimobjectData(SimConnect sender, SIMCONNECT_RECV_SIMOBJECT_DATA data)
    {
        lock (_lock)
        {
            switch ((DataRequestId)data.dwRequestID)
            {
                case DataRequestId.HighFrequency:
                    ApplyHighFrequencyData((HighFrequencyData)data.dwData[0]);
                    break;

                case DataRequestId.LowFrequency:
                    ApplyLowFrequencyData((LowFrequencyData)data.dwData[0]);
                    break;

                case DataRequestId.EngineData:
                    ApplyEngineData((EngineDataStruct)data.dwData[0]);
                    break;

                case DataRequestId.AircraftTitle:
                    var titleData = (AircraftTitleData)data.dwData[0];
                    CurrentState.Aircraft = titleData.Title ?? string.Empty;
                    break;
            }

            CurrentState.Timestamp = DateTimeOffset.UtcNow;
        }

        StateUpdated?.Invoke(CurrentState);
    }

    private void ApplyHighFrequencyData(HighFrequencyData d)
    {
        CurrentState.Position.Latitude = d.PlaneLatitude;
        CurrentState.Position.Longitude = d.PlaneLongitude;
        CurrentState.Position.AltitudeMsl = d.PlaneAltitude;
        CurrentState.Position.AltitudeAgl = d.PlaneAltAboveGround;

        CurrentState.Attitude.Pitch = d.PlanePitchDegrees;
        CurrentState.Attitude.Bank = d.PlaneBankDegrees;
        CurrentState.Attitude.HeadingTrue = d.PlaneHeadingTrue;
        CurrentState.Attitude.HeadingMagnetic = d.PlaneHeadingMagnetic;

        CurrentState.Speeds.IndicatedAirspeed = d.AirspeedIndicated;
        CurrentState.Speeds.TrueAirspeed = d.AirspeedTrue;
        CurrentState.Speeds.GroundSpeed = d.GroundVelocity;
        CurrentState.Speeds.Mach = d.AirspeedMach;
        CurrentState.Speeds.VerticalSpeed = d.VerticalSpeed;
    }

    private void ApplyLowFrequencyData(LowFrequencyData d)
    {
        CurrentState.Autopilot.Master = d.AutopilotMaster != 0;
        CurrentState.Autopilot.Heading = d.AutopilotHeading;
        CurrentState.Autopilot.Altitude = d.AutopilotAltitude;
        CurrentState.Autopilot.VerticalSpeed = d.AutopilotVerticalSpeed;
        CurrentState.Autopilot.Airspeed = d.AutopilotAirspeed;

        CurrentState.Radios.Com1 = d.Com1Frequency;
        CurrentState.Radios.Com2 = d.Com2Frequency;
        CurrentState.Radios.Nav1 = d.Nav1Frequency;
        CurrentState.Radios.Nav2 = d.Nav2Frequency;

        CurrentState.Fuel.TotalGallons = d.FuelTotalQuantity;
        CurrentState.Fuel.TotalWeightLbs = d.FuelTotalWeight;

        CurrentState.Surfaces.GearHandle = d.GearHandlePosition != 0;
        CurrentState.Surfaces.FlapsPercent = d.FlapsPercent;
        CurrentState.Surfaces.SpoilersPercent = d.SpoilersPercent;

        CurrentState.Environment.WindSpeedKts = d.WindVelocity;
        CurrentState.Environment.WindDirection = d.WindDirection;
        CurrentState.Environment.VisibilitySm = d.Visibility;
        CurrentState.Environment.TemperatureC = d.AmbientTemperature;
        CurrentState.Environment.BarometerInHg = d.BarometerPressure;
    }

    private void ApplyEngineData(EngineDataStruct d)
    {
        ApplyOneEngine(CurrentState.Engines.Engines[0],
            d.Eng1Rpm, d.Eng1ManifoldPressure, d.Eng1FuelFlow, d.Eng1Egt, d.Eng1OilTemp, d.Eng1OilPressure);
        ApplyOneEngine(CurrentState.Engines.Engines[1],
            d.Eng2Rpm, d.Eng2ManifoldPressure, d.Eng2FuelFlow, d.Eng2Egt, d.Eng2OilTemp, d.Eng2OilPressure);
        ApplyOneEngine(CurrentState.Engines.Engines[2],
            d.Eng3Rpm, d.Eng3ManifoldPressure, d.Eng3FuelFlow, d.Eng3Egt, d.Eng3OilTemp, d.Eng3OilPressure);
        ApplyOneEngine(CurrentState.Engines.Engines[3],
            d.Eng4Rpm, d.Eng4ManifoldPressure, d.Eng4FuelFlow, d.Eng4Egt, d.Eng4OilTemp, d.Eng4OilPressure);

        // Infer active engine count from RPM > 0
        int count = 0;
        for (int i = 0; i < 4; i++)
        {
            if (CurrentState.Engines.Engines[i].Rpm > 1.0)
                count = i + 1;
        }
        CurrentState.Engines.EngineCount = count;
    }

    private static void ApplyOneEngine(EngineParams ep,
        double rpm, double mp, double ff, double egt, double oilTemp, double oilPressure)
    {
        ep.Rpm = rpm;
        ep.ManifoldPressure = mp;
        ep.FuelFlowGph = ff;
        ep.ExhaustGasTemp = egt;
        ep.OilTemp = oilTemp;
        ep.OilPressure = oilPressure;
    }

    private void HandleDisconnect()
    {
        if (!_connected) return;
        _connected = false;
        _flightActive = false;
        _dataSubscribed = false;
        CurrentState.Connected = false;
        StopTimers();

        // Dispose the old SimConnect instance so a fresh Connect() can
        // create a new one.
        if (_simConnect is not null)
        {
            try { _simConnect.Dispose(); }
            catch { /* best-effort cleanup */ }
            _simConnect = null;
        }

        ConnectionChanged?.Invoke(false);
        Log("WARN", "Connection lost. Auto-reconnect will engage if enabled.");
    }
}
