using System.Runtime.InteropServices;
using Microsoft.FlightSimulator.SimConnect;

namespace SimConnectBridge.Models;

/// <summary>
/// Enumerations used to identify SimConnect data definition groups and request IDs.
/// </summary>
public enum DataDefinitionId
{
    HighFrequency,
    LowFrequency,
    AircraftTitle,
    EngineData
}

/// <summary>
/// Request IDs for SimConnect data requests.
/// </summary>
public enum DataRequestId
{
    HighFrequency,
    LowFrequency,
    AircraftTitle,
    EngineData
}

/// <summary>
/// Event IDs for SimConnect system event subscriptions.
/// </summary>
public enum SimEventId
{
    FlightLoaded,
    SimStart,
    SimStop,
    Paused,
    Unpaused,
}

// ---------------------------------------------------------------------------
//  High-frequency struct: position, attitude, speeds, vertical speed
//  Polled at ~30 Hz
// ---------------------------------------------------------------------------

/// <summary>
/// SimConnect data struct for high-frequency telemetry (position, attitude, speeds).
/// Fields are ordered to match the data definition registration order.
/// </summary>
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct HighFrequencyData
{
    // Position
    public double PlaneLatitude;           // PLANE LATITUDE, degrees
    public double PlaneLongitude;          // PLANE LONGITUDE, degrees
    public double PlaneAltitude;           // PLANE ALTITUDE, feet
    public double PlaneAltAboveGround;     // PLANE ALT ABOVE GROUND, feet

    // Attitude
    public double PlanePitchDegrees;       // PLANE PITCH DEGREES, degrees
    public double PlaneBankDegrees;        // PLANE BANK DEGREES, degrees
    public double PlaneHeadingTrue;        // PLANE HEADING DEGREES TRUE, degrees
    public double PlaneHeadingMagnetic;    // PLANE HEADING DEGREES MAGNETIC, degrees

    // Speeds
    public double AirspeedIndicated;       // AIRSPEED INDICATED, knots
    public double AirspeedTrue;            // AIRSPEED TRUE, knots
    public double GroundVelocity;          // GROUND VELOCITY, knots
    public double AirspeedMach;            // AIRSPEED MACH, mach

    // Vertical
    public double VerticalSpeed;           // VERTICAL SPEED, feet per minute
}

// ---------------------------------------------------------------------------
//  Low-frequency struct: autopilot, radios, fuel, surfaces, environment
//  Polled at ~1 Hz
// ---------------------------------------------------------------------------

/// <summary>
/// SimConnect data struct for low-frequency telemetry (autopilot, radios, fuel, surfaces, environment).
/// </summary>
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct LowFrequencyData
{
    // Autopilot
    public int AutopilotMaster;            // AUTOPILOT MASTER, bool (0/1)
    public double AutopilotHeading;        // AUTOPILOT HEADING LOCK DIR, degrees
    public double AutopilotAltitude;       // AUTOPILOT ALTITUDE LOCK VAR, feet
    public double AutopilotVerticalSpeed;  // AUTOPILOT VERTICAL HOLD VAR, feet per minute
    public double AutopilotAirspeed;       // AUTOPILOT AIRSPEED HOLD VAR, knots

    // Radios (frequencies in MHz returned as BCD16 or Hz depending on sim version)
    public double Com1Frequency;           // COM ACTIVE FREQUENCY:1, MHz
    public double Com2Frequency;           // COM ACTIVE FREQUENCY:2, MHz
    public double Nav1Frequency;           // NAV ACTIVE FREQUENCY:1, MHz
    public double Nav2Frequency;           // NAV ACTIVE FREQUENCY:2, MHz

    // Fuel
    public double FuelTotalQuantity;       // FUEL TOTAL QUANTITY, gallons
    public double FuelTotalWeight;         // FUEL TOTAL QUANTITY WEIGHT, pounds

    // Surfaces
    public int GearHandlePosition;         // GEAR HANDLE POSITION, bool (0/1)
    public double FlapsPercent;            // TRAILING EDGE FLAPS LEFT PERCENT, percent
    public double SpoilersPercent;         // SPOILERS HANDLE POSITION, percent

    // Environment
    public double WindVelocity;            // AMBIENT WIND VELOCITY, knots
    public double WindDirection;           // AMBIENT WIND DIRECTION, degrees
    public double Visibility;              // AMBIENT VISIBILITY, statute miles
    public double AmbientTemperature;      // AMBIENT TEMPERATURE, celsius
    public double BarometerPressure;       // BAROMETER PRESSURE, inches of mercury
}

// ---------------------------------------------------------------------------
//  Engine data struct: per-engine variables for up to 4 engines
// ---------------------------------------------------------------------------

/// <summary>
/// SimConnect data struct for engine parameters (up to 4 engines).
/// </summary>
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct EngineDataStruct
{
    // Engine 1
    public double Eng1Rpm;
    public double Eng1ManifoldPressure;
    public double Eng1FuelFlow;
    public double Eng1Egt;
    public double Eng1OilTemp;
    public double Eng1OilPressure;

    // Engine 2
    public double Eng2Rpm;
    public double Eng2ManifoldPressure;
    public double Eng2FuelFlow;
    public double Eng2Egt;
    public double Eng2OilTemp;
    public double Eng2OilPressure;

    // Engine 3
    public double Eng3Rpm;
    public double Eng3ManifoldPressure;
    public double Eng3FuelFlow;
    public double Eng3Egt;
    public double Eng3OilTemp;
    public double Eng3OilPressure;

    // Engine 4
    public double Eng4Rpm;
    public double Eng4ManifoldPressure;
    public double Eng4FuelFlow;
    public double Eng4Egt;
    public double Eng4OilTemp;
    public double Eng4OilPressure;
}

/// <summary>
/// SimConnect data struct for retrieving the aircraft title string.
/// </summary>
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct AircraftTitleData
{
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
    public string Title;
}
