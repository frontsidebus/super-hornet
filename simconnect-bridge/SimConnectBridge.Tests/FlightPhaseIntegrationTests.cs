using System.Text.Json;
using System.Text.Json.Serialization;
using FluentAssertions;
using SimConnectBridge.Models;
using Xunit;

namespace SimConnectBridge.Tests;

/// <summary>
/// Tests that the C# SimState model correctly populates all fields needed
/// for flight phase detection on the Python side. The Python flight phase
/// detector relies on: gear state, altitude AGL, ground speed, vertical speed,
/// and flap position. These tests verify those fields are present, properly
/// named in JSON, and can represent all relevant flight phases.
/// </summary>
public class FlightPhaseIntegrationTests
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false
    };

    // -----------------------------------------------------------------------
    //  Required fields present in JSON
    // -----------------------------------------------------------------------

    [Fact]
    public void SimState_ContainsGearHandleField()
    {
        var state = new SimState { Surfaces = new SurfaceData { GearHandle = true } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("surfaces")
            .TryGetProperty("gear_handle", out var gearHandle).Should().BeTrue();
        gearHandle.GetBoolean().Should().BeTrue();
    }

    [Fact]
    public void SimState_ContainsAltitudeAglField()
    {
        var state = new SimState { Position = new PositionData { AltitudeAgl = 150 } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("position")
            .TryGetProperty("altitude_agl", out var agl).Should().BeTrue();
        agl.GetDouble().Should().Be(150);
    }

    [Fact]
    public void SimState_ContainsGroundSpeedField()
    {
        var state = new SimState { Speeds = new SpeedData { GroundSpeed = 65 } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("speeds")
            .TryGetProperty("ground_speed", out var gs).Should().BeTrue();
        gs.GetDouble().Should().Be(65);
    }

    [Fact]
    public void SimState_ContainsVerticalSpeedField()
    {
        var state = new SimState { Speeds = new SpeedData { VerticalSpeed = -700 } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("speeds")
            .TryGetProperty("vertical_speed", out var vs).Should().BeTrue();
        vs.GetDouble().Should().Be(-700);
    }

    [Fact]
    public void SimState_ContainsFlapsPercentField()
    {
        var state = new SimState { Surfaces = new SurfaceData { FlapsPercent = 30 } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("surfaces")
            .TryGetProperty("flaps_percent", out var flaps).Should().BeTrue();
        flaps.GetDouble().Should().Be(30);
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Parked / Preflight
    // -----------------------------------------------------------------------

    [Fact]
    public void ParkedState_HasZeroSpeedAndOnGround()
    {
        var state = CreateParkedState();

        state.Speeds.GroundSpeed.Should().Be(0);
        state.Speeds.VerticalSpeed.Should().Be(0);
        state.Position.AltitudeAgl.Should().BeLessThan(50);
        state.Surfaces.GearHandle.Should().BeTrue("gear should be down when parked");
    }

    [Fact]
    public void ParkedState_SerializesAllRequiredFields()
    {
        var state = CreateParkedState();
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        AssertFlightPhaseFieldsPresent(doc);
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Taxi
    // -----------------------------------------------------------------------

    [Fact]
    public void TaxiState_HasLowGroundSpeedOnGround()
    {
        var state = CreateTaxiState();

        state.Speeds.GroundSpeed.Should().BeInRange(1, 30, "taxi speed is typically under 30 knots");
        state.Speeds.VerticalSpeed.Should().Be(0);
        state.Position.AltitudeAgl.Should().BeLessThan(50);
        state.Surfaces.GearHandle.Should().BeTrue("gear should be down while taxiing");
        state.Surfaces.FlapsPercent.Should().BeGreaterOrEqualTo(0);
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Takeoff
    // -----------------------------------------------------------------------

    [Fact]
    public void TakeoffState_HasHighGroundSpeedAndPositiveVerticalSpeed()
    {
        var state = CreateTakeoffState();

        state.Speeds.GroundSpeed.Should().BeGreaterThan(50, "takeoff roll speed is typically above 50 knots");
        state.Speeds.VerticalSpeed.Should().BeGreaterThan(0, "positive vertical speed during takeoff climb");
        state.Position.AltitudeAgl.Should().BeLessThan(1000, "still near the ground during initial takeoff");
        state.Surfaces.GearHandle.Should().BeTrue("gear typically still down immediately after rotation");
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Climb
    // -----------------------------------------------------------------------

    [Fact]
    public void ClimbState_HasPositiveVerticalSpeedAndAltitude()
    {
        var state = CreateClimbState();

        state.Speeds.VerticalSpeed.Should().BeGreaterThan(100, "positive vertical speed during climb");
        state.Position.AltitudeAgl.Should().BeGreaterThan(1000);
        state.Surfaces.GearHandle.Should().BeFalse("gear retracted during climb");
        state.Surfaces.FlapsPercent.Should().Be(0, "flaps typically retracted during normal climb");
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Cruise
    // -----------------------------------------------------------------------

    [Fact]
    public void CruiseState_HasStableAltitudeAndMinimalVerticalSpeed()
    {
        var state = CreateCruiseState();

        state.Speeds.VerticalSpeed.Should().BeInRange(-100, 100, "near-zero vertical speed in cruise");
        state.Position.AltitudeAgl.Should().BeGreaterThan(3000);
        state.Surfaces.GearHandle.Should().BeFalse("gear retracted in cruise");
        state.Surfaces.FlapsPercent.Should().Be(0, "clean configuration in cruise");
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Descent
    // -----------------------------------------------------------------------

    [Fact]
    public void DescentState_HasNegativeVerticalSpeed()
    {
        var state = CreateDescentState();

        state.Speeds.VerticalSpeed.Should().BeLessThan(-100, "negative vertical speed during descent");
        state.Position.AltitudeAgl.Should().BeGreaterThan(1000);
        state.Surfaces.GearHandle.Should().BeFalse("gear typically retracted during high descent");
        state.Surfaces.FlapsPercent.Should().Be(0);
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Approach
    // -----------------------------------------------------------------------

    [Fact]
    public void ApproachState_HasGearDownFlapsExtendedLowAltitude()
    {
        var state = CreateApproachState();

        state.Speeds.VerticalSpeed.Should().BeLessThan(0, "descending on approach");
        state.Position.AltitudeAgl.Should().BeLessThan(3000);
        state.Position.AltitudeAgl.Should().BeGreaterThan(200);
        state.Surfaces.GearHandle.Should().BeTrue("gear down on approach");
        state.Surfaces.FlapsPercent.Should().BeGreaterThan(0, "flaps extended on approach");
    }

    // -----------------------------------------------------------------------
    //  Flight phase: Landing
    // -----------------------------------------------------------------------

    [Fact]
    public void LandingState_HasVeryLowAltitudeGearDownFlapsDown()
    {
        var state = CreateLandingState();

        state.Position.AltitudeAgl.Should().BeLessThan(100, "very low AGL during landing");
        state.Surfaces.GearHandle.Should().BeTrue("gear must be down for landing");
        state.Surfaces.FlapsPercent.Should().BeGreaterThan(0, "flaps extended for landing");
        state.Speeds.VerticalSpeed.Should().BeLessThan(0, "still descending toward touchdown");
        state.Speeds.GroundSpeed.Should().BeGreaterThan(40, "approach speed");
    }

    // -----------------------------------------------------------------------
    //  Full mapping from struct data to SimState for phase detection
    // -----------------------------------------------------------------------

    [Fact]
    public void FullStructMapping_ProducesValidPhaseDetectionData()
    {
        // Simulate the complete data path from SimConnect structs to SimState
        var hfData = new HighFrequencyData
        {
            PlaneAltAboveGround = 500,
            GroundVelocity = 130,
            VerticalSpeed = -700,
            PlaneLatitude = 47.6,
            PlaneLongitude = -122.3,
            PlaneAltitude = 1200,
            AirspeedIndicated = 120,
            AirspeedTrue = 128,
            AirspeedMach = 0.19,
            PlanePitchDegrees = -3.0,
            PlaneBankDegrees = 0,
            PlaneHeadingTrue = 180,
            PlaneHeadingMagnetic = 165
        };

        var lfData = new LowFrequencyData
        {
            GearHandlePosition = 1,
            FlapsPercent = 40.0,
            SpoilersPercent = 0,
            AutopilotMaster = 0,
            AutopilotHeading = 180,
            AutopilotAltitude = 3000,
            AutopilotVerticalSpeed = 0,
            AutopilotAirspeed = 120,
            Com1Frequency = 119.1,
            Com2Frequency = 121.5,
            Nav1Frequency = 110.5,
            Nav2Frequency = 112.0,
            FuelTotalQuantity = 30.0,
            FuelTotalWeight = 180.0,
            WindVelocity = 8,
            WindDirection = 220,
            Visibility = 10.0,
            AmbientTemperature = 15.0,
            BarometerPressure = 29.92
        };

        // Apply (mirrors SimConnectManager.Apply*Data methods)
        var state = new SimState();
        state.Position.AltitudeAgl = hfData.PlaneAltAboveGround;
        state.Position.AltitudeMsl = hfData.PlaneAltitude;
        state.Position.Latitude = hfData.PlaneLatitude;
        state.Position.Longitude = hfData.PlaneLongitude;
        state.Speeds.GroundSpeed = hfData.GroundVelocity;
        state.Speeds.VerticalSpeed = hfData.VerticalSpeed;
        state.Speeds.IndicatedAirspeed = hfData.AirspeedIndicated;
        state.Speeds.TrueAirspeed = hfData.AirspeedTrue;
        state.Speeds.Mach = hfData.AirspeedMach;
        state.Attitude.Pitch = hfData.PlanePitchDegrees;
        state.Attitude.Bank = hfData.PlaneBankDegrees;
        state.Attitude.HeadingTrue = hfData.PlaneHeadingTrue;
        state.Attitude.HeadingMagnetic = hfData.PlaneHeadingMagnetic;
        state.Surfaces.GearHandle = lfData.GearHandlePosition != 0;
        state.Surfaces.FlapsPercent = lfData.FlapsPercent;
        state.Surfaces.SpoilersPercent = lfData.SpoilersPercent;

        // Verify the five critical fields for phase detection
        state.Position.AltitudeAgl.Should().Be(500);
        state.Speeds.GroundSpeed.Should().Be(130);
        state.Speeds.VerticalSpeed.Should().Be(-700);
        state.Surfaces.GearHandle.Should().BeTrue();
        state.Surfaces.FlapsPercent.Should().Be(40.0);

        // Verify they serialize correctly for the Python consumer
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        AssertFlightPhaseFieldsPresent(doc);

        doc.RootElement.GetProperty("position").GetProperty("altitude_agl").GetDouble().Should().Be(500);
        doc.RootElement.GetProperty("speeds").GetProperty("ground_speed").GetDouble().Should().Be(130);
        doc.RootElement.GetProperty("speeds").GetProperty("vertical_speed").GetDouble().Should().Be(-700);
        doc.RootElement.GetProperty("surfaces").GetProperty("gear_handle").GetBoolean().Should().BeTrue();
        doc.RootElement.GetProperty("surfaces").GetProperty("flaps_percent").GetDouble().Should().Be(40.0);
    }

    // -----------------------------------------------------------------------
    //  Boundary and edge cases
    // -----------------------------------------------------------------------

    [Theory]
    [InlineData(0, "on the ground")]
    [InlineData(0.5, "just barely airborne")]
    [InlineData(50, "low altitude")]
    [InlineData(500, "pattern altitude")]
    [InlineData(10000, "cruise altitude")]
    [InlineData(45000, "high altitude")]
    public void AltitudeAgl_VariousValues_SerializesCorrectly(double altitudeAgl, string description)
    {
        var state = new SimState { Position = new PositionData { AltitudeAgl = altitudeAgl } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("position")
            .GetProperty("altitude_agl").GetDouble()
            .Should().Be(altitudeAgl, $"altitude AGL for {description} should serialize correctly");
    }

    [Theory]
    [InlineData(-3000, "rapid descent")]
    [InlineData(-700, "approach descent")]
    [InlineData(-100, "slight descent")]
    [InlineData(0, "level")]
    [InlineData(100, "slight climb")]
    [InlineData(700, "normal climb")]
    [InlineData(3000, "rapid climb")]
    public void VerticalSpeed_VariousValues_SerializesCorrectly(double vs, string description)
    {
        var state = new SimState { Speeds = new SpeedData { VerticalSpeed = vs } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("speeds")
            .GetProperty("vertical_speed").GetDouble()
            .Should().Be(vs, $"vertical speed for {description} should serialize correctly");
    }

    [Theory]
    [InlineData(0, "clean")]
    [InlineData(10, "approach flaps")]
    [InlineData(20, "partial flaps")]
    [InlineData(40, "landing flaps")]
    [InlineData(100, "full flaps")]
    public void FlapsPercent_VariousValues_SerializesCorrectly(double flaps, string description)
    {
        var state = new SimState { Surfaces = new SurfaceData { FlapsPercent = flaps } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("surfaces")
            .GetProperty("flaps_percent").GetDouble()
            .Should().Be(flaps, $"flaps percent for {description} should serialize correctly");
    }

    [Theory]
    [InlineData(true, "gear down")]
    [InlineData(false, "gear up")]
    public void GearHandle_BothStates_SerializeCorrectly(bool gearDown, string description)
    {
        var state = new SimState { Surfaces = new SurfaceData { GearHandle = gearDown } };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("surfaces")
            .GetProperty("gear_handle").GetBoolean()
            .Should().Be(gearDown, $"{description} should serialize correctly");
    }

    // -----------------------------------------------------------------------
    //  Filtered subscription with phase-relevant fields
    // -----------------------------------------------------------------------

    [Fact]
    public void FilteredSubscription_PositionSpeedsSurfaces_ContainsAllPhaseFields()
    {
        var state = CreateApproachState();

        var fields = new List<string> { "position", "speeds", "surfaces" };
        var dict = new Dictionary<string, object?>
        {
            ["timestamp"] = state.Timestamp,
            ["connected"] = state.Connected
        };

        foreach (var field in fields)
        {
            switch (field)
            {
                case "position": dict["position"] = state.Position; break;
                case "speeds": dict["speeds"] = state.Speeds; break;
                case "surfaces": dict["surfaces"] = state.Surfaces; break;
            }
        }

        var json = JsonSerializer.Serialize(dict, JsonOptions);
        var doc = JsonDocument.Parse(json);

        // All five phase-detection fields should be present
        doc.RootElement.GetProperty("position").TryGetProperty("altitude_agl", out _).Should().BeTrue();
        doc.RootElement.GetProperty("speeds").TryGetProperty("ground_speed", out _).Should().BeTrue();
        doc.RootElement.GetProperty("speeds").TryGetProperty("vertical_speed", out _).Should().BeTrue();
        doc.RootElement.GetProperty("surfaces").TryGetProperty("gear_handle", out _).Should().BeTrue();
        doc.RootElement.GetProperty("surfaces").TryGetProperty("flaps_percent", out _).Should().BeTrue();
    }

    // -----------------------------------------------------------------------
    //  Helpers: flight phase state factories
    // -----------------------------------------------------------------------

    private static void AssertFlightPhaseFieldsPresent(JsonDocument doc)
    {
        doc.RootElement.GetProperty("position").TryGetProperty("altitude_agl", out _).Should().BeTrue("altitude_agl is required for phase detection");
        doc.RootElement.GetProperty("speeds").TryGetProperty("ground_speed", out _).Should().BeTrue("ground_speed is required for phase detection");
        doc.RootElement.GetProperty("speeds").TryGetProperty("vertical_speed", out _).Should().BeTrue("vertical_speed is required for phase detection");
        doc.RootElement.GetProperty("surfaces").TryGetProperty("gear_handle", out _).Should().BeTrue("gear_handle is required for phase detection");
        doc.RootElement.GetProperty("surfaces").TryGetProperty("flaps_percent", out _).Should().BeTrue("flaps_percent is required for phase detection");
    }

    private static SimState CreateParkedState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 0, AltitudeMsl = 430 },
        Speeds = new SpeedData { GroundSpeed = 0, VerticalSpeed = 0, IndicatedAirspeed = 0 },
        Surfaces = new SurfaceData { GearHandle = true, FlapsPercent = 0, SpoilersPercent = 0 }
    };

    private static SimState CreateTaxiState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 0, AltitudeMsl = 430 },
        Speeds = new SpeedData { GroundSpeed = 15, VerticalSpeed = 0, IndicatedAirspeed = 12 },
        Surfaces = new SurfaceData { GearHandle = true, FlapsPercent = 10, SpoilersPercent = 0 }
    };

    private static SimState CreateTakeoffState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 100, AltitudeMsl = 530 },
        Speeds = new SpeedData { GroundSpeed = 75, VerticalSpeed = 800, IndicatedAirspeed = 80 },
        Surfaces = new SurfaceData { GearHandle = true, FlapsPercent = 10, SpoilersPercent = 0 }
    };

    private static SimState CreateClimbState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 3000, AltitudeMsl = 3430 },
        Speeds = new SpeedData { GroundSpeed = 100, VerticalSpeed = 500, IndicatedAirspeed = 90 },
        Surfaces = new SurfaceData { GearHandle = false, FlapsPercent = 0, SpoilersPercent = 0 }
    };

    private static SimState CreateCruiseState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 5000, AltitudeMsl = 5430 },
        Speeds = new SpeedData { GroundSpeed = 120, VerticalSpeed = 0, IndicatedAirspeed = 110 },
        Surfaces = new SurfaceData { GearHandle = false, FlapsPercent = 0, SpoilersPercent = 0 }
    };

    private static SimState CreateDescentState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 3000, AltitudeMsl = 3430 },
        Speeds = new SpeedData { GroundSpeed = 120, VerticalSpeed = -500, IndicatedAirspeed = 110 },
        Surfaces = new SurfaceData { GearHandle = false, FlapsPercent = 0, SpoilersPercent = 0 }
    };

    private static SimState CreateApproachState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 1500, AltitudeMsl = 1930 },
        Speeds = new SpeedData { GroundSpeed = 90, VerticalSpeed = -500, IndicatedAirspeed = 85 },
        Surfaces = new SurfaceData { GearHandle = true, FlapsPercent = 20, SpoilersPercent = 0 }
    };

    private static SimState CreateLandingState() => new()
    {
        Connected = true,
        Aircraft = "Cessna 172",
        Position = new PositionData { AltitudeAgl = 50, AltitudeMsl = 480 },
        Speeds = new SpeedData { GroundSpeed = 65, VerticalSpeed = -300, IndicatedAirspeed = 65 },
        Surfaces = new SurfaceData { GearHandle = true, FlapsPercent = 40, SpoilersPercent = 0 }
    };
}
