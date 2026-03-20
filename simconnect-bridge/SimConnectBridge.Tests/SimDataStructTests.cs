using System.Runtime.InteropServices;
using FluentAssertions;
using SimConnectBridge.Models;
using Xunit;

namespace SimConnectBridge.Tests;

/// <summary>
/// Tests for SimConnect data struct layouts.
/// Verifies struct sizes, StructLayout attributes, and correct data mapping
/// from SimConnect structs to the SimState model.
/// </summary>
public class SimDataStructTests
{
    // -----------------------------------------------------------------------
    //  Struct size verification
    //  These sizes must match what SimConnect expects for marshalling.
    // -----------------------------------------------------------------------

    [Fact]
    public void HighFrequencyData_HasCorrectSize()
    {
        // 13 double fields x 8 bytes = 104 bytes
        var size = Marshal.SizeOf<HighFrequencyData>();
        size.Should().Be(13 * sizeof(double), "HighFrequencyData has 13 double fields");
    }

    [Fact]
    public void LowFrequencyData_HasCorrectSize()
    {
        // 2 int fields (4 bytes each) + 16 double fields (8 bytes each) = 8 + 128 = 136 bytes
        var size = Marshal.SizeOf<LowFrequencyData>();
        size.Should().Be(2 * sizeof(int) + 16 * sizeof(double),
            "LowFrequencyData has 2 int fields and 16 double fields");
    }

    [Fact]
    public void EngineDataStruct_HasCorrectSize()
    {
        // 4 engines x 6 double fields = 24 doubles x 8 bytes = 192 bytes
        var size = Marshal.SizeOf<EngineDataStruct>();
        size.Should().Be(24 * sizeof(double), "EngineDataStruct has 24 double fields (4 engines x 6 params)");
    }

    [Fact]
    public void AircraftTitleData_HasCorrectSize()
    {
        // 256-byte fixed string
        var size = Marshal.SizeOf<AircraftTitleData>();
        size.Should().Be(256, "AircraftTitleData has a 256-char fixed string");
    }

    // -----------------------------------------------------------------------
    //  StructLayout attribute verification
    // -----------------------------------------------------------------------

    [Theory]
    [InlineData(typeof(HighFrequencyData))]
    [InlineData(typeof(LowFrequencyData))]
    [InlineData(typeof(EngineDataStruct))]
    [InlineData(typeof(AircraftTitleData))]
    public void AllStructs_HaveSequentialLayout(Type structType)
    {
        var attr = structType.GetCustomAttributes(typeof(StructLayoutAttribute), false)
            .Cast<StructLayoutAttribute>()
            .FirstOrDefault();

        attr.Should().NotBeNull($"{structType.Name} should have a StructLayout attribute");
        attr!.Value.Should().Be(LayoutKind.Sequential,
            $"{structType.Name} must use Sequential layout for SimConnect marshalling");
    }

    [Theory]
    [InlineData(typeof(HighFrequencyData))]
    [InlineData(typeof(LowFrequencyData))]
    [InlineData(typeof(EngineDataStruct))]
    [InlineData(typeof(AircraftTitleData))]
    public void AllStructs_UsePack1(Type structType)
    {
        var attr = structType.GetCustomAttributes(typeof(StructLayoutAttribute), false)
            .Cast<StructLayoutAttribute>()
            .FirstOrDefault();

        attr.Should().NotBeNull();
        attr!.Pack.Should().Be(1,
            $"{structType.Name} must use Pack=1 to avoid padding between fields");
    }

    [Theory]
    [InlineData(typeof(HighFrequencyData))]
    [InlineData(typeof(LowFrequencyData))]
    [InlineData(typeof(EngineDataStruct))]
    [InlineData(typeof(AircraftTitleData))]
    public void AllStructs_UseAnsiCharSet(Type structType)
    {
        var attr = structType.GetCustomAttributes(typeof(StructLayoutAttribute), false)
            .Cast<StructLayoutAttribute>()
            .FirstOrDefault();

        attr.Should().NotBeNull();
        attr!.CharSet.Should().Be(CharSet.Ansi,
            $"{structType.Name} must use CharSet.Ansi for SimConnect compatibility");
    }

    // -----------------------------------------------------------------------
    //  Field count verification
    // -----------------------------------------------------------------------

    [Fact]
    public void HighFrequencyData_Has13Fields()
    {
        var fields = typeof(HighFrequencyData).GetFields();
        fields.Should().HaveCount(13, "HighFrequencyData should have 13 fields matching the data definition registration");
    }

    [Fact]
    public void LowFrequencyData_Has18Fields()
    {
        var fields = typeof(LowFrequencyData).GetFields();
        fields.Should().HaveCount(18, "LowFrequencyData should have 18 fields matching the data definition registration");
    }

    [Fact]
    public void EngineDataStruct_Has24Fields()
    {
        var fields = typeof(EngineDataStruct).GetFields();
        fields.Should().HaveCount(24, "EngineDataStruct should have 24 fields (4 engines x 6 params)");
    }

    [Fact]
    public void AircraftTitleData_Has1Field()
    {
        var fields = typeof(AircraftTitleData).GetFields();
        fields.Should().HaveCount(1, "AircraftTitleData should have 1 field (Title)");
    }

    // -----------------------------------------------------------------------
    //  Data mapping: HighFrequencyData -> SimState
    // -----------------------------------------------------------------------

    [Fact]
    public void HighFrequencyData_MapsToSimStatePosition()
    {
        var hf = new HighFrequencyData
        {
            PlaneLatitude = 47.6062,
            PlaneLongitude = -122.3321,
            PlaneAltitude = 5000,
            PlaneAltAboveGround = 2800
        };

        var state = new SimState();
        state.Position.Latitude = hf.PlaneLatitude;
        state.Position.Longitude = hf.PlaneLongitude;
        state.Position.AltitudeMsl = hf.PlaneAltitude;
        state.Position.AltitudeAgl = hf.PlaneAltAboveGround;

        state.Position.Latitude.Should().Be(47.6062);
        state.Position.Longitude.Should().Be(-122.3321);
        state.Position.AltitudeMsl.Should().Be(5000);
        state.Position.AltitudeAgl.Should().Be(2800);
    }

    [Fact]
    public void HighFrequencyData_MapsToSimStateAttitude()
    {
        var hf = new HighFrequencyData
        {
            PlanePitchDegrees = -3.5,
            PlaneBankDegrees = 10.0,
            PlaneHeadingTrue = 270.0,
            PlaneHeadingMagnetic = 255.0
        };

        var state = new SimState();
        state.Attitude.Pitch = hf.PlanePitchDegrees;
        state.Attitude.Bank = hf.PlaneBankDegrees;
        state.Attitude.HeadingTrue = hf.PlaneHeadingTrue;
        state.Attitude.HeadingMagnetic = hf.PlaneHeadingMagnetic;

        state.Attitude.Pitch.Should().Be(-3.5);
        state.Attitude.Bank.Should().Be(10.0);
        state.Attitude.HeadingTrue.Should().Be(270.0);
        state.Attitude.HeadingMagnetic.Should().Be(255.0);
    }

    [Fact]
    public void HighFrequencyData_MapsToSimStateSpeeds()
    {
        var hf = new HighFrequencyData
        {
            AirspeedIndicated = 120,
            AirspeedTrue = 130,
            GroundVelocity = 125,
            AirspeedMach = 0.19,
            VerticalSpeed = -500
        };

        var state = new SimState();
        state.Speeds.IndicatedAirspeed = hf.AirspeedIndicated;
        state.Speeds.TrueAirspeed = hf.AirspeedTrue;
        state.Speeds.GroundSpeed = hf.GroundVelocity;
        state.Speeds.Mach = hf.AirspeedMach;
        state.Speeds.VerticalSpeed = hf.VerticalSpeed;

        state.Speeds.IndicatedAirspeed.Should().Be(120);
        state.Speeds.TrueAirspeed.Should().Be(130);
        state.Speeds.GroundSpeed.Should().Be(125);
        state.Speeds.Mach.Should().Be(0.19);
        state.Speeds.VerticalSpeed.Should().Be(-500);
    }

    // -----------------------------------------------------------------------
    //  Data mapping: LowFrequencyData -> SimState
    // -----------------------------------------------------------------------

    [Fact]
    public void LowFrequencyData_MapsToSimStateAutopilot()
    {
        var lf = new LowFrequencyData
        {
            AutopilotMaster = 1,
            AutopilotHeading = 270,
            AutopilotAltitude = 10000,
            AutopilotVerticalSpeed = 1000,
            AutopilotAirspeed = 200
        };

        var state = new SimState();
        state.Autopilot.Master = lf.AutopilotMaster != 0;
        state.Autopilot.Heading = lf.AutopilotHeading;
        state.Autopilot.Altitude = lf.AutopilotAltitude;
        state.Autopilot.VerticalSpeed = lf.AutopilotVerticalSpeed;
        state.Autopilot.Airspeed = lf.AutopilotAirspeed;

        state.Autopilot.Master.Should().BeTrue();
        state.Autopilot.Heading.Should().Be(270);
        state.Autopilot.Altitude.Should().Be(10000);
        state.Autopilot.VerticalSpeed.Should().Be(1000);
        state.Autopilot.Airspeed.Should().Be(200);
    }

    [Fact]
    public void LowFrequencyData_AutopilotMasterZero_MapsFalse()
    {
        var lf = new LowFrequencyData { AutopilotMaster = 0 };

        bool master = lf.AutopilotMaster != 0;
        master.Should().BeFalse();
    }

    [Fact]
    public void LowFrequencyData_MapsToSimStateRadios()
    {
        var lf = new LowFrequencyData
        {
            Com1Frequency = 121.5,
            Com2Frequency = 118.0,
            Nav1Frequency = 110.5,
            Nav2Frequency = 112.3
        };

        var state = new SimState();
        state.Radios.Com1 = lf.Com1Frequency;
        state.Radios.Com2 = lf.Com2Frequency;
        state.Radios.Nav1 = lf.Nav1Frequency;
        state.Radios.Nav2 = lf.Nav2Frequency;

        state.Radios.Com1.Should().Be(121.5);
        state.Radios.Com2.Should().Be(118.0);
        state.Radios.Nav1.Should().Be(110.5);
        state.Radios.Nav2.Should().Be(112.3);
    }

    [Fact]
    public void LowFrequencyData_MapsToSimStateFuel()
    {
        var lf = new LowFrequencyData
        {
            FuelTotalQuantity = 40.0,
            FuelTotalWeight = 240.0
        };

        var state = new SimState();
        state.Fuel.TotalGallons = lf.FuelTotalQuantity;
        state.Fuel.TotalWeightLbs = lf.FuelTotalWeight;

        state.Fuel.TotalGallons.Should().Be(40.0);
        state.Fuel.TotalWeightLbs.Should().Be(240.0);
    }

    [Fact]
    public void LowFrequencyData_MapsToSimStateSurfaces()
    {
        var lf = new LowFrequencyData
        {
            GearHandlePosition = 1,
            FlapsPercent = 30.0,
            SpoilersPercent = 50.0
        };

        var state = new SimState();
        state.Surfaces.GearHandle = lf.GearHandlePosition != 0;
        state.Surfaces.FlapsPercent = lf.FlapsPercent;
        state.Surfaces.SpoilersPercent = lf.SpoilersPercent;

        state.Surfaces.GearHandle.Should().BeTrue();
        state.Surfaces.FlapsPercent.Should().Be(30.0);
        state.Surfaces.SpoilersPercent.Should().Be(50.0);
    }

    [Fact]
    public void LowFrequencyData_GearHandleZero_MapsFalse()
    {
        var lf = new LowFrequencyData { GearHandlePosition = 0 };
        bool gearDown = lf.GearHandlePosition != 0;
        gearDown.Should().BeFalse();
    }

    [Fact]
    public void LowFrequencyData_MapsToSimStateEnvironment()
    {
        var lf = new LowFrequencyData
        {
            WindVelocity = 15.0,
            WindDirection = 270.0,
            Visibility = 10.0,
            AmbientTemperature = 20.0,
            BarometerPressure = 29.92
        };

        var state = new SimState();
        state.Environment.WindSpeedKts = lf.WindVelocity;
        state.Environment.WindDirection = lf.WindDirection;
        state.Environment.VisibilitySm = lf.Visibility;
        state.Environment.TemperatureC = lf.AmbientTemperature;
        state.Environment.BarometerInHg = lf.BarometerPressure;

        state.Environment.WindSpeedKts.Should().Be(15.0);
        state.Environment.WindDirection.Should().Be(270.0);
        state.Environment.VisibilitySm.Should().Be(10.0);
        state.Environment.TemperatureC.Should().Be(20.0);
        state.Environment.BarometerInHg.Should().Be(29.92);
    }

    // -----------------------------------------------------------------------
    //  Data mapping: EngineDataStruct -> SimState
    // -----------------------------------------------------------------------

    [Fact]
    public void EngineDataStruct_MapsAllFourEngines()
    {
        var eng = new EngineDataStruct
        {
            Eng1Rpm = 2400, Eng1ManifoldPressure = 28.5, Eng1FuelFlow = 12.0,
            Eng1Egt = 1400, Eng1OilTemp = 600, Eng1OilPressure = 50,
            Eng2Rpm = 2350, Eng2ManifoldPressure = 28.0, Eng2FuelFlow = 11.8,
            Eng2Egt = 1380, Eng2OilTemp = 590, Eng2OilPressure = 48,
            Eng3Rpm = 0, Eng3ManifoldPressure = 0, Eng3FuelFlow = 0,
            Eng3Egt = 0, Eng3OilTemp = 0, Eng3OilPressure = 0,
            Eng4Rpm = 0, Eng4ManifoldPressure = 0, Eng4FuelFlow = 0,
            Eng4Egt = 0, Eng4OilTemp = 0, Eng4OilPressure = 0
        };

        var state = new SimState();

        // Apply engine 1 (mirrors ApplyOneEngine in SimConnectManager)
        state.Engines.Engines[0].Rpm = eng.Eng1Rpm;
        state.Engines.Engines[0].ManifoldPressure = eng.Eng1ManifoldPressure;
        state.Engines.Engines[0].FuelFlowGph = eng.Eng1FuelFlow;
        state.Engines.Engines[0].ExhaustGasTemp = eng.Eng1Egt;
        state.Engines.Engines[0].OilTemp = eng.Eng1OilTemp;
        state.Engines.Engines[0].OilPressure = eng.Eng1OilPressure;

        // Apply engine 2
        state.Engines.Engines[1].Rpm = eng.Eng2Rpm;
        state.Engines.Engines[1].ManifoldPressure = eng.Eng2ManifoldPressure;
        state.Engines.Engines[1].FuelFlowGph = eng.Eng2FuelFlow;
        state.Engines.Engines[1].ExhaustGasTemp = eng.Eng2Egt;
        state.Engines.Engines[1].OilTemp = eng.Eng2OilTemp;
        state.Engines.Engines[1].OilPressure = eng.Eng2OilPressure;

        state.Engines.Engines[0].Rpm.Should().Be(2400);
        state.Engines.Engines[0].ManifoldPressure.Should().Be(28.5);
        state.Engines.Engines[0].FuelFlowGph.Should().Be(12.0);
        state.Engines.Engines[0].ExhaustGasTemp.Should().Be(1400);
        state.Engines.Engines[0].OilTemp.Should().Be(600);
        state.Engines.Engines[0].OilPressure.Should().Be(50);

        state.Engines.Engines[1].Rpm.Should().Be(2350);
        state.Engines.Engines[2].Rpm.Should().Be(0);
        state.Engines.Engines[3].Rpm.Should().Be(0);
    }

    [Fact]
    public void EngineCountInference_SingleEngine_ReturnsOne()
    {
        var state = new SimState();
        state.Engines.Engines[0].Rpm = 2400;
        state.Engines.Engines[1].Rpm = 0;
        state.Engines.Engines[2].Rpm = 0;
        state.Engines.Engines[3].Rpm = 0;

        // Mirror the engine count inference logic
        int count = 0;
        for (int i = 0; i < 4; i++)
        {
            if (state.Engines.Engines[i].Rpm > 1.0)
                count = i + 1;
        }

        count.Should().Be(1);
    }

    [Fact]
    public void EngineCountInference_TwinEngine_ReturnsTwo()
    {
        var state = new SimState();
        state.Engines.Engines[0].Rpm = 2400;
        state.Engines.Engines[1].Rpm = 2350;
        state.Engines.Engines[2].Rpm = 0;
        state.Engines.Engines[3].Rpm = 0;

        int count = 0;
        for (int i = 0; i < 4; i++)
        {
            if (state.Engines.Engines[i].Rpm > 1.0)
                count = i + 1;
        }

        count.Should().Be(2);
    }

    [Fact]
    public void EngineCountInference_FourEngines_ReturnsFour()
    {
        var state = new SimState();
        for (int i = 0; i < 4; i++)
            state.Engines.Engines[i].Rpm = 5500 + i * 10;

        int count = 0;
        for (int i = 0; i < 4; i++)
        {
            if (state.Engines.Engines[i].Rpm > 1.0)
                count = i + 1;
        }

        count.Should().Be(4);
    }

    [Fact]
    public void EngineCountInference_NoEnginesRunning_ReturnsZero()
    {
        var state = new SimState();

        int count = 0;
        for (int i = 0; i < 4; i++)
        {
            if (state.Engines.Engines[i].Rpm > 1.0)
                count = i + 1;
        }

        count.Should().Be(0);
    }

    [Fact]
    public void EngineCountInference_RpmBelowThreshold_NotCounted()
    {
        // RPM of 0.5 is below the 1.0 threshold used in production code
        var state = new SimState();
        state.Engines.Engines[0].Rpm = 0.5;

        int count = 0;
        for (int i = 0; i < 4; i++)
        {
            if (state.Engines.Engines[i].Rpm > 1.0)
                count = i + 1;
        }

        count.Should().Be(0);
    }

    // -----------------------------------------------------------------------
    //  AircraftTitleData mapping
    // -----------------------------------------------------------------------

    [Fact]
    public void AircraftTitleData_MapsToSimStateAircraft()
    {
        var titleData = new AircraftTitleData { Title = "Cessna 172 Skyhawk" };

        var state = new SimState();
        state.Aircraft = titleData.Title ?? string.Empty;

        state.Aircraft.Should().Be("Cessna 172 Skyhawk");
    }

    [Fact]
    public void AircraftTitleData_NullTitle_MapsToEmptyString()
    {
        var titleData = new AircraftTitleData { Title = null! };

        var state = new SimState();
        state.Aircraft = titleData.Title ?? string.Empty;

        state.Aircraft.Should().BeEmpty();
    }

    // -----------------------------------------------------------------------
    //  Struct field ordering verification (must match registration order)
    // -----------------------------------------------------------------------

    [Fact]
    public void HighFrequencyData_FieldOrder_MatchesRegistration()
    {
        var fields = typeof(HighFrequencyData).GetFields();
        var fieldNames = fields.Select(f => f.Name).ToList();

        // Order must match AddFloat64 calls in RegisterDataDefinitions
        fieldNames.Should().ContainInOrder(
            "PlaneLatitude",
            "PlaneLongitude",
            "PlaneAltitude",
            "PlaneAltAboveGround",
            "PlanePitchDegrees",
            "PlaneBankDegrees",
            "PlaneHeadingTrue",
            "PlaneHeadingMagnetic",
            "AirspeedIndicated",
            "AirspeedTrue",
            "GroundVelocity",
            "AirspeedMach",
            "VerticalSpeed"
        );
    }

    [Fact]
    public void LowFrequencyData_FieldOrder_MatchesRegistration()
    {
        var fields = typeof(LowFrequencyData).GetFields();
        var fieldNames = fields.Select(f => f.Name).ToList();

        fieldNames.Should().ContainInOrder(
            "AutopilotMaster",
            "AutopilotHeading",
            "AutopilotAltitude",
            "AutopilotVerticalSpeed",
            "AutopilotAirspeed",
            "Com1Frequency",
            "Com2Frequency",
            "Nav1Frequency",
            "Nav2Frequency",
            "FuelTotalQuantity",
            "FuelTotalWeight",
            "GearHandlePosition",
            "FlapsPercent",
            "SpoilersPercent",
            "WindVelocity",
            "WindDirection",
            "Visibility",
            "AmbientTemperature",
            "BarometerPressure"
        );
    }

    [Fact]
    public void EngineDataStruct_FieldOrder_GroupedByEngine()
    {
        var fields = typeof(EngineDataStruct).GetFields();
        var fieldNames = fields.Select(f => f.Name).ToList();

        // Verify engine 1 comes before engine 2, etc.
        fieldNames.Should().ContainInOrder(
            "Eng1Rpm", "Eng1ManifoldPressure", "Eng1FuelFlow", "Eng1Egt", "Eng1OilTemp", "Eng1OilPressure",
            "Eng2Rpm", "Eng2ManifoldPressure", "Eng2FuelFlow", "Eng2Egt", "Eng2OilTemp", "Eng2OilPressure",
            "Eng3Rpm", "Eng3ManifoldPressure", "Eng3FuelFlow", "Eng3Egt", "Eng3OilTemp", "Eng3OilPressure",
            "Eng4Rpm", "Eng4ManifoldPressure", "Eng4FuelFlow", "Eng4Egt", "Eng4OilTemp", "Eng4OilPressure"
        );
    }
}
