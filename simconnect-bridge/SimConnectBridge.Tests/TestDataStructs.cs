using System.Runtime.InteropServices;

namespace SimConnectBridge.Tests;

/// <summary>
/// Mirror copies of the SimConnect data structs from SimDataStructs.cs.
/// These are defined here so tests can run without the SimConnect SDK installed.
/// Any changes to the production structs should be reflected here.
/// </summary>

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct HighFrequencyData
{
    // Position
    public double PlaneLatitude;
    public double PlaneLongitude;
    public double PlaneAltitude;
    public double PlaneAltAboveGround;

    // Attitude
    public double PlanePitchDegrees;
    public double PlaneBankDegrees;
    public double PlaneHeadingTrue;
    public double PlaneHeadingMagnetic;

    // Speeds
    public double AirspeedIndicated;
    public double AirspeedTrue;
    public double GroundVelocity;
    public double AirspeedMach;

    // Vertical
    public double VerticalSpeed;
}

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct LowFrequencyData
{
    // Autopilot
    public int AutopilotMaster;
    public double AutopilotHeading;
    public double AutopilotAltitude;
    public double AutopilotVerticalSpeed;
    public double AutopilotAirspeed;

    // Radios
    public double Com1Frequency;
    public double Com2Frequency;
    public double Nav1Frequency;
    public double Nav2Frequency;

    // Fuel
    public double FuelTotalQuantity;
    public double FuelTotalWeight;

    // Surfaces
    public int GearHandlePosition;
    public double FlapsPercent;
    public double SpoilersPercent;

    // Environment
    public double WindVelocity;
    public double WindDirection;
    public double Visibility;
    public double AmbientTemperature;
    public double BarometerPressure;
}

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct EngineDataStruct
{
    public double Eng1Rpm;
    public double Eng1ManifoldPressure;
    public double Eng1FuelFlow;
    public double Eng1Egt;
    public double Eng1OilTemp;
    public double Eng1OilPressure;

    public double Eng2Rpm;
    public double Eng2ManifoldPressure;
    public double Eng2FuelFlow;
    public double Eng2Egt;
    public double Eng2OilTemp;
    public double Eng2OilPressure;

    public double Eng3Rpm;
    public double Eng3ManifoldPressure;
    public double Eng3FuelFlow;
    public double Eng3Egt;
    public double Eng3OilTemp;
    public double Eng3OilPressure;

    public double Eng4Rpm;
    public double Eng4ManifoldPressure;
    public double Eng4FuelFlow;
    public double Eng4Egt;
    public double Eng4OilTemp;
    public double Eng4OilPressure;
}

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 1)]
public struct AircraftTitleData
{
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
    public string Title;
}
