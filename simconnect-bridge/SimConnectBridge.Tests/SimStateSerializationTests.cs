using System.Text.Json;
using System.Text.Json.Serialization;
using FluentAssertions;
using SimConnectBridge.Models;
using Xunit;

namespace SimConnectBridge.Tests;

/// <summary>
/// Tests for SimState JSON serialization/deserialization.
/// Verifies snake_case naming, correct nesting, round-trip fidelity,
/// and default value behavior.
/// </summary>
public class SimStateSerializationTests
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false
    };

    // -----------------------------------------------------------------------
    //  Snake-case property naming
    // -----------------------------------------------------------------------

    [Fact]
    public void Serialize_UsesSnakeCasePropertyNames()
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);

        json.Should().Contain("\"timestamp\"");
        json.Should().Contain("\"connected\"");
        json.Should().Contain("\"aircraft\"");
        json.Should().Contain("\"position\"");
        json.Should().Contain("\"attitude\"");
        json.Should().Contain("\"speeds\"");
        json.Should().Contain("\"engines\"");
        json.Should().Contain("\"autopilot\"");
        json.Should().Contain("\"radios\"");
        json.Should().Contain("\"fuel\"");
        json.Should().Contain("\"surfaces\"");
        json.Should().Contain("\"environment\"");
    }

    [Fact]
    public void Serialize_PositionData_UsesSnakeCaseNestedPropertyNames()
    {
        var state = new SimState
        {
            Position = new PositionData
            {
                Latitude = 47.6,
                Longitude = -122.3,
                AltitudeMsl = 5000,
                AltitudeAgl = 2500
            }
        };

        var json = JsonSerializer.Serialize(state, JsonOptions);

        json.Should().Contain("\"latitude\"");
        json.Should().Contain("\"longitude\"");
        json.Should().Contain("\"altitude_msl\"");
        json.Should().Contain("\"altitude_agl\"");
    }

    [Fact]
    public void Serialize_SpeedData_UsesSnakeCaseNestedPropertyNames()
    {
        var state = new SimState
        {
            Speeds = new SpeedData
            {
                IndicatedAirspeed = 120,
                TrueAirspeed = 130,
                GroundSpeed = 125,
                Mach = 0.19,
                VerticalSpeed = -500
            }
        };

        var json = JsonSerializer.Serialize(state, JsonOptions);

        json.Should().Contain("\"indicated_airspeed\"");
        json.Should().Contain("\"true_airspeed\"");
        json.Should().Contain("\"ground_speed\"");
        json.Should().Contain("\"mach\"");
        json.Should().Contain("\"vertical_speed\"");
    }

    [Fact]
    public void Serialize_EngineData_UsesSnakeCaseNestedPropertyNames()
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);

        json.Should().Contain("\"engine_count\"");
        json.Should().Contain("\"rpm\"");
        json.Should().Contain("\"manifold_pressure\"");
        json.Should().Contain("\"fuel_flow_gph\"");
        json.Should().Contain("\"egt\"");
        json.Should().Contain("\"oil_temp\"");
        json.Should().Contain("\"oil_pressure\"");
    }

    [Fact]
    public void Serialize_SurfaceData_UsesSnakeCasePropertyNames()
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);

        json.Should().Contain("\"gear_handle\"");
        json.Should().Contain("\"flaps_percent\"");
        json.Should().Contain("\"spoilers_percent\"");
    }

    [Fact]
    public void Serialize_EnvironmentData_UsesSnakeCasePropertyNames()
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);

        json.Should().Contain("\"wind_speed_kts\"");
        json.Should().Contain("\"wind_direction\"");
        json.Should().Contain("\"visibility_sm\"");
        json.Should().Contain("\"temperature_c\"");
        json.Should().Contain("\"barometer_inhg\"");
    }

    // -----------------------------------------------------------------------
    //  Correct nesting structure
    // -----------------------------------------------------------------------

    [Fact]
    public void Serialize_ProducesCorrectTopLevelStructure()
    {
        var state = new SimState { Connected = true, Aircraft = "Cessna 172" };
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        root.TryGetProperty("timestamp", out _).Should().BeTrue();
        root.TryGetProperty("connected", out var connected).Should().BeTrue();
        connected.GetBoolean().Should().BeTrue();
        root.TryGetProperty("aircraft", out var aircraft).Should().BeTrue();
        aircraft.GetString().Should().Be("Cessna 172");
        root.TryGetProperty("position", out var pos).Should().BeTrue();
        pos.ValueKind.Should().Be(JsonValueKind.Object);
        root.TryGetProperty("engines", out var eng).Should().BeTrue();
        eng.ValueKind.Should().Be(JsonValueKind.Object);
    }

    [Fact]
    public void Serialize_EnginesArray_HasFourElements()
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        var engines = doc.RootElement.GetProperty("engines").GetProperty("engines");
        engines.GetArrayLength().Should().Be(4);
    }

    // -----------------------------------------------------------------------
    //  Round-trip serialization
    // -----------------------------------------------------------------------

    [Fact]
    public void RoundTrip_FullState_PreservesAllValues()
    {
        var original = CreatePopulatedState();

        var json = JsonSerializer.Serialize(original, JsonOptions);
        var deserialized = JsonSerializer.Deserialize<SimState>(json, JsonOptions);

        deserialized.Should().NotBeNull();
        deserialized!.Connected.Should().Be(original.Connected);
        deserialized.Aircraft.Should().Be(original.Aircraft);

        deserialized.Position.Latitude.Should().Be(original.Position.Latitude);
        deserialized.Position.Longitude.Should().Be(original.Position.Longitude);
        deserialized.Position.AltitudeMsl.Should().Be(original.Position.AltitudeMsl);
        deserialized.Position.AltitudeAgl.Should().Be(original.Position.AltitudeAgl);

        deserialized.Attitude.Pitch.Should().Be(original.Attitude.Pitch);
        deserialized.Attitude.Bank.Should().Be(original.Attitude.Bank);
        deserialized.Attitude.HeadingTrue.Should().Be(original.Attitude.HeadingTrue);
        deserialized.Attitude.HeadingMagnetic.Should().Be(original.Attitude.HeadingMagnetic);

        deserialized.Speeds.IndicatedAirspeed.Should().Be(original.Speeds.IndicatedAirspeed);
        deserialized.Speeds.TrueAirspeed.Should().Be(original.Speeds.TrueAirspeed);
        deserialized.Speeds.GroundSpeed.Should().Be(original.Speeds.GroundSpeed);
        deserialized.Speeds.Mach.Should().Be(original.Speeds.Mach);
        deserialized.Speeds.VerticalSpeed.Should().Be(original.Speeds.VerticalSpeed);

        deserialized.Autopilot.Master.Should().Be(original.Autopilot.Master);
        deserialized.Autopilot.Heading.Should().Be(original.Autopilot.Heading);
        deserialized.Autopilot.Altitude.Should().Be(original.Autopilot.Altitude);
        deserialized.Autopilot.VerticalSpeed.Should().Be(original.Autopilot.VerticalSpeed);
        deserialized.Autopilot.Airspeed.Should().Be(original.Autopilot.Airspeed);

        deserialized.Radios.Com1.Should().Be(original.Radios.Com1);
        deserialized.Radios.Com2.Should().Be(original.Radios.Com2);
        deserialized.Radios.Nav1.Should().Be(original.Radios.Nav1);
        deserialized.Radios.Nav2.Should().Be(original.Radios.Nav2);

        deserialized.Fuel.TotalGallons.Should().Be(original.Fuel.TotalGallons);
        deserialized.Fuel.TotalWeightLbs.Should().Be(original.Fuel.TotalWeightLbs);

        deserialized.Surfaces.GearHandle.Should().Be(original.Surfaces.GearHandle);
        deserialized.Surfaces.FlapsPercent.Should().Be(original.Surfaces.FlapsPercent);
        deserialized.Surfaces.SpoilersPercent.Should().Be(original.Surfaces.SpoilersPercent);

        deserialized.Environment.WindSpeedKts.Should().Be(original.Environment.WindSpeedKts);
        deserialized.Environment.WindDirection.Should().Be(original.Environment.WindDirection);
        deserialized.Environment.VisibilitySm.Should().Be(original.Environment.VisibilitySm);
        deserialized.Environment.TemperatureC.Should().Be(original.Environment.TemperatureC);
        deserialized.Environment.BarometerInHg.Should().Be(original.Environment.BarometerInHg);
    }

    [Fact]
    public void RoundTrip_EngineData_PreservesAllFourEngines()
    {
        var original = new SimState();
        original.Engines.EngineCount = 2;
        original.Engines.Engines[0].Rpm = 2400;
        original.Engines.Engines[0].FuelFlowGph = 12.5;
        original.Engines.Engines[1].Rpm = 2350;
        original.Engines.Engines[1].FuelFlowGph = 12.3;
        // Engines 3 and 4 stay at zero

        var json = JsonSerializer.Serialize(original, JsonOptions);
        var deserialized = JsonSerializer.Deserialize<SimState>(json, JsonOptions);

        deserialized.Should().NotBeNull();
        deserialized!.Engines.EngineCount.Should().Be(2);
        deserialized.Engines.Engines.Should().HaveCount(4);
        deserialized.Engines.Engines[0].Rpm.Should().Be(2400);
        deserialized.Engines.Engines[0].FuelFlowGph.Should().Be(12.5);
        deserialized.Engines.Engines[1].Rpm.Should().Be(2350);
        deserialized.Engines.Engines[2].Rpm.Should().Be(0);
        deserialized.Engines.Engines[3].Rpm.Should().Be(0);
    }

    // -----------------------------------------------------------------------
    //  Default values
    // -----------------------------------------------------------------------

    [Fact]
    public void DefaultState_HasExpectedDefaults()
    {
        var state = new SimState();

        state.Connected.Should().BeFalse();
        state.Aircraft.Should().BeEmpty();
        state.Position.Latitude.Should().Be(0);
        state.Position.Longitude.Should().Be(0);
        state.Position.AltitudeMsl.Should().Be(0);
        state.Position.AltitudeAgl.Should().Be(0);
        state.Speeds.IndicatedAirspeed.Should().Be(0);
        state.Speeds.VerticalSpeed.Should().Be(0);
        state.Autopilot.Master.Should().BeFalse();
        state.Surfaces.GearHandle.Should().BeFalse();
        state.Surfaces.FlapsPercent.Should().Be(0);
        state.Engines.EngineCount.Should().Be(0);
        state.Engines.Engines.Should().HaveCount(4);
    }

    [Fact]
    public void DefaultState_TimestampIsRecent()
    {
        var before = DateTimeOffset.UtcNow;
        var state = new SimState();
        var after = DateTimeOffset.UtcNow;

        state.Timestamp.Should().BeOnOrAfter(before);
        state.Timestamp.Should().BeOnOrBefore(after);
    }

    [Fact]
    public void DefaultState_AllEngineParamsAreZero()
    {
        var state = new SimState();

        foreach (var engine in state.Engines.Engines)
        {
            engine.Rpm.Should().Be(0);
            engine.ManifoldPressure.Should().Be(0);
            engine.FuelFlowGph.Should().Be(0);
            engine.ExhaustGasTemp.Should().Be(0);
            engine.OilTemp.Should().Be(0);
            engine.OilPressure.Should().Be(0);
        }
    }

    // -----------------------------------------------------------------------
    //  Partial data scenarios
    // -----------------------------------------------------------------------

    [Fact]
    public void Serialize_SingleEngineAircraft_ContainsOnlyOneActiveEngine()
    {
        var state = new SimState();
        state.Engines.EngineCount = 1;
        state.Engines.Engines[0].Rpm = 2400;
        state.Engines.Engines[0].ManifoldPressure = 28.5;
        state.Engines.Engines[0].FuelFlowGph = 10.0;

        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);
        var engines = doc.RootElement.GetProperty("engines");

        engines.GetProperty("engine_count").GetInt32().Should().Be(1);

        var engineArray = engines.GetProperty("engines");
        engineArray.GetArrayLength().Should().Be(4);
        engineArray[0].GetProperty("rpm").GetDouble().Should().Be(2400);
        engineArray[1].GetProperty("rpm").GetDouble().Should().Be(0);
        engineArray[2].GetProperty("rpm").GetDouble().Should().Be(0);
        engineArray[3].GetProperty("rpm").GetDouble().Should().Be(0);
    }

    [Fact]
    public void Serialize_MultiEngineAircraft_ContainsMultipleActiveEngines()
    {
        var state = new SimState();
        state.Engines.EngineCount = 4;
        for (int i = 0; i < 4; i++)
        {
            state.Engines.Engines[i].Rpm = 5500 + i * 10;
            state.Engines.Engines[i].FuelFlowGph = 800 + i * 5;
        }

        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);
        var engines = doc.RootElement.GetProperty("engines");

        engines.GetProperty("engine_count").GetInt32().Should().Be(4);

        var engineArray = engines.GetProperty("engines");
        for (int i = 0; i < 4; i++)
        {
            engineArray[i].GetProperty("rpm").GetDouble().Should().Be(5500 + i * 10);
            engineArray[i].GetProperty("fuel_flow_gph").GetDouble().Should().Be(800 + i * 5);
        }
    }

    [Fact]
    public void Deserialize_PartialJson_PositionOnly_SetsFieldsCorrectly()
    {
        var json = """
        {
            "connected": true,
            "aircraft": "Boeing 747",
            "position": {
                "latitude": 51.47,
                "longitude": -0.46,
                "altitude_msl": 35000,
                "altitude_agl": 34500
            }
        }
        """;

        var state = JsonSerializer.Deserialize<SimState>(json, JsonOptions);

        state.Should().NotBeNull();
        state!.Connected.Should().BeTrue();
        state.Aircraft.Should().Be("Boeing 747");
        state.Position.Latitude.Should().Be(51.47);
        state.Position.Longitude.Should().Be(-0.46);
        state.Position.AltitudeMsl.Should().Be(35000);
        state.Position.AltitudeAgl.Should().Be(34500);
    }

    [Fact]
    public void Deserialize_MinimalJson_CreatesValidObject()
    {
        var json = "{}";

        var state = JsonSerializer.Deserialize<SimState>(json, JsonOptions);

        state.Should().NotBeNull();
        state!.Connected.Should().BeFalse();
        state.Aircraft.Should().BeNull();
    }

    // -----------------------------------------------------------------------
    //  JsonPropertyName attribute correctness
    // -----------------------------------------------------------------------

    [Theory]
    [InlineData("timestamp")]
    [InlineData("connected")]
    [InlineData("aircraft")]
    [InlineData("position")]
    [InlineData("attitude")]
    [InlineData("speeds")]
    [InlineData("engines")]
    [InlineData("autopilot")]
    [InlineData("radios")]
    [InlineData("fuel")]
    [InlineData("surfaces")]
    [InlineData("environment")]
    public void Serialize_TopLevelProperties_UseExplicitJsonPropertyNames(string expectedPropertyName)
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.TryGetProperty(expectedPropertyName, out _).Should().BeTrue(
            $"expected top-level property '{expectedPropertyName}' to be present");
    }

    [Theory]
    [InlineData("heading_true")]
    [InlineData("heading_magnetic")]
    public void Serialize_AttitudeData_ContainsExpectedSnakeCaseNames(string expectedPropertyName)
    {
        var state = new SimState();
        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);
        var attitude = doc.RootElement.GetProperty("attitude");

        attitude.TryGetProperty(expectedPropertyName, out _).Should().BeTrue(
            $"expected attitude property '{expectedPropertyName}' to be present");
    }

    // -----------------------------------------------------------------------
    //  Helpers
    // -----------------------------------------------------------------------

    private static SimState CreatePopulatedState()
    {
        return new SimState
        {
            Connected = true,
            Aircraft = "Cessna 172 Skyhawk",
            Position = new PositionData
            {
                Latitude = 47.6062,
                Longitude = -122.3321,
                AltitudeMsl = 5000,
                AltitudeAgl = 2800
            },
            Attitude = new AttitudeData
            {
                Pitch = -2.5,
                Bank = 15.0,
                HeadingTrue = 270.0,
                HeadingMagnetic = 255.0
            },
            Speeds = new SpeedData
            {
                IndicatedAirspeed = 120,
                TrueAirspeed = 130,
                GroundSpeed = 125,
                Mach = 0.19,
                VerticalSpeed = -500
            },
            Autopilot = new AutopilotState
            {
                Master = true,
                Heading = 270,
                Altitude = 5000,
                VerticalSpeed = -500,
                Airspeed = 120
            },
            Radios = new RadioData
            {
                Com1 = 121.5,
                Com2 = 118.0,
                Nav1 = 110.5,
                Nav2 = 112.3
            },
            Fuel = new FuelData
            {
                TotalGallons = 40.0,
                TotalWeightLbs = 240.0
            },
            Surfaces = new SurfaceData
            {
                GearHandle = true,
                FlapsPercent = 20.0,
                SpoilersPercent = 0.0
            },
            Environment = new EnvironmentData
            {
                WindSpeedKts = 10.0,
                WindDirection = 180.0,
                VisibilitySm = 10.0,
                TemperatureC = 15.0,
                BarometerInHg = 29.92
            }
        };
    }
}
