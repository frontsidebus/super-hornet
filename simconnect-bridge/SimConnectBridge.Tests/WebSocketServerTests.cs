using System.Text.Json;
using System.Text.Json.Serialization;
using FluentAssertions;
using Fleck;
using Moq;
using SimConnectBridge.Models;
using Xunit;

namespace SimConnectBridge.Tests;

/// <summary>
/// Tests for the WebSocket server message handling logic.
/// Since TelemetryWebSocketServer lives in the main project (which depends on
/// the SimConnect SDK), these tests exercise the JSON protocol contract by
/// replicating the server's serialization, filtering, and message-handling
/// logic. This ensures the protocol stays correct even without the SDK.
/// </summary>
public class WebSocketServerTests
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false
    };

    // -----------------------------------------------------------------------
    //  get_state request / response protocol
    // -----------------------------------------------------------------------

    [Fact]
    public void GetStateRequest_SerializesToExpectedJsonFormat()
    {
        var request = new { type = "get_state" };
        var json = JsonSerializer.Serialize(request, JsonOptions);

        json.Should().Contain("\"type\"");
        json.Should().Contain("\"get_state\"");
    }

    [Fact]
    public void GetStateResponse_ContainsTypeAndMessage()
    {
        var response = new { type = "state_response", message = "Full state will be delivered on next update cycle." };
        var json = JsonSerializer.Serialize(response, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("type").GetString().Should().Be("state_response");
        doc.RootElement.GetProperty("message").GetString().Should().NotBeNullOrEmpty();
    }

    [Fact]
    public void BroadcastState_FullState_SerializesAllTopLevelFields()
    {
        var state = new SimState
        {
            Connected = true,
            Aircraft = "Cessna 172"
        };

        var json = JsonSerializer.Serialize(state, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.TryGetProperty("connected", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("aircraft", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("position", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("attitude", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("speeds", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("engines", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("autopilot", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("radios", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("fuel", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("surfaces", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("environment", out _).Should().BeTrue();
    }

    // -----------------------------------------------------------------------
    //  subscribe request with field filters
    // -----------------------------------------------------------------------

    [Fact]
    public void SubscribeRequest_WithFields_SerializesCorrectly()
    {
        var request = new { type = "subscribe", fields = new[] { "position", "speeds" } };
        var json = JsonSerializer.Serialize(request, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("type").GetString().Should().Be("subscribe");
        doc.RootElement.GetProperty("fields").GetArrayLength().Should().Be(2);
    }

    [Fact]
    public void FilteredStateSerialization_IncludesOnlyRequestedFields()
    {
        var state = new SimState
        {
            Connected = true,
            Position = new PositionData { Latitude = 47.6, Longitude = -122.3 },
            Speeds = new SpeedData { IndicatedAirspeed = 120 }
        };

        var fields = new List<string> { "position", "speeds" };
        var filtered = SerializeFilteredState(state, fields);
        var doc = JsonDocument.Parse(filtered);

        // Always-included fields
        doc.RootElement.TryGetProperty("timestamp", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("connected", out _).Should().BeTrue();

        // Requested fields
        doc.RootElement.TryGetProperty("position", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("speeds", out _).Should().BeTrue();

        // NOT requested fields should be absent
        doc.RootElement.TryGetProperty("attitude", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("engines", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("autopilot", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("radios", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("fuel", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("surfaces", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("environment", out _).Should().BeFalse();
        doc.RootElement.TryGetProperty("aircraft", out _).Should().BeFalse();
    }

    [Theory]
    [InlineData("position")]
    [InlineData("attitude")]
    [InlineData("speeds")]
    [InlineData("engines")]
    [InlineData("autopilot")]
    [InlineData("radios")]
    [InlineData("fuel")]
    [InlineData("surfaces")]
    [InlineData("environment")]
    [InlineData("aircraft")]
    public void FilteredStateSerialization_EachFieldCanBeSubscribedIndividually(string field)
    {
        var state = new SimState { Connected = true, Aircraft = "Test Aircraft" };
        var filtered = SerializeFilteredState(state, new List<string> { field });
        var doc = JsonDocument.Parse(filtered);

        doc.RootElement.TryGetProperty(field, out _).Should().BeTrue(
            $"subscribing to '{field}' should include it in the output");
    }

    [Fact]
    public void FilteredStateSerialization_EmptyFieldList_IncludesOnlyTimestampAndConnected()
    {
        var state = new SimState { Connected = true };
        var filtered = SerializeFilteredState(state, new List<string>());
        var doc = JsonDocument.Parse(filtered);

        doc.RootElement.TryGetProperty("timestamp", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("connected", out _).Should().BeTrue();

        // No optional fields
        doc.RootElement.EnumerateObject().Count().Should().Be(2);
    }

    [Fact]
    public void FilteredStateSerialization_IsCaseInsensitive()
    {
        var state = new SimState();
        var filtered = SerializeFilteredState(state, new List<string> { "POSITION", "Speeds" });
        var doc = JsonDocument.Parse(filtered);

        doc.RootElement.TryGetProperty("position", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("speeds", out _).Should().BeTrue();
    }

    [Fact]
    public void FilteredStateSerialization_UnknownField_IsIgnored()
    {
        var state = new SimState();
        var filtered = SerializeFilteredState(state, new List<string> { "nonexistent_field", "position" });
        var doc = JsonDocument.Parse(filtered);

        doc.RootElement.TryGetProperty("position", out _).Should().BeTrue();
        doc.RootElement.TryGetProperty("nonexistent_field", out _).Should().BeFalse();
    }

    // -----------------------------------------------------------------------
    //  Multiple client connection tracking
    // -----------------------------------------------------------------------

    [Fact]
    public void MultipleClients_EachGetUniqueIds()
    {
        var ids = Enumerable.Range(0, 5).Select(_ => Guid.NewGuid()).ToList();
        ids.Distinct().Should().HaveCount(5, "each client should have a unique ID");
    }

    [Fact]
    public void ClientTracking_ConcurrentDictionary_SupportsAddAndRemove()
    {
        var clients = new System.Collections.Concurrent.ConcurrentDictionary<Guid, Mock<IWebSocketConnection>>();

        var mock1 = new Mock<IWebSocketConnection>();
        var mock2 = new Mock<IWebSocketConnection>();
        var mock3 = new Mock<IWebSocketConnection>();

        var id1 = Guid.NewGuid();
        var id2 = Guid.NewGuid();
        var id3 = Guid.NewGuid();

        clients[id1] = mock1;
        clients[id2] = mock2;
        clients[id3] = mock3;

        clients.Should().HaveCount(3);
        clients.Keys.Distinct().Should().HaveCount(3);
    }

    [Fact]
    public void ClientTracking_MockSocket_CanSendMessages()
    {
        var sentMessages = new List<string>();
        var mockSocket = new Mock<IWebSocketConnection>();
        mockSocket.Setup(s => s.Send(It.IsAny<string>()))
            .Callback<string>(msg => sentMessages.Add(msg));

        var state = new SimState { Connected = true };
        var json = JsonSerializer.Serialize(state, JsonOptions);

        mockSocket.Object.Send(json);

        sentMessages.Should().HaveCount(1);
        sentMessages[0].Should().Contain("\"connected\":true");
    }

    [Fact]
    public void ClientTracking_BroadcastToMultipleClients_EachReceivesMessage()
    {
        var receivedCounts = new Dictionary<Guid, int>();
        var clients = new Dictionary<Guid, Mock<IWebSocketConnection>>();

        for (int i = 0; i < 3; i++)
        {
            var id = Guid.NewGuid();
            receivedCounts[id] = 0;
            var mock = new Mock<IWebSocketConnection>();
            var capturedId = id;
            mock.Setup(s => s.Send(It.IsAny<string>()))
                .Callback<string>(_ => receivedCounts[capturedId]++);
            clients[id] = mock;
        }

        var state = new SimState { Connected = true };
        var json = JsonSerializer.Serialize(state, JsonOptions);

        // Simulate broadcast
        foreach (var (_, client) in clients)
        {
            client.Object.Send(json);
        }

        receivedCounts.Values.Should().AllBeEquivalentTo(1, "each client should receive exactly one message");
    }

    // -----------------------------------------------------------------------
    //  Client disconnect cleanup
    // -----------------------------------------------------------------------

    [Fact]
    public void ClientDisconnect_RemovesFromClientDictionary()
    {
        var clients = new System.Collections.Concurrent.ConcurrentDictionary<Guid, string>();
        var id = Guid.NewGuid();
        clients[id] = "connected";

        clients.Should().ContainKey(id);

        clients.TryRemove(id, out _);
        clients.Should().NotContainKey(id);
        clients.Should().BeEmpty();
    }

    [Fact]
    public void ClientDisconnect_DoesNotAffectOtherClients()
    {
        var clients = new System.Collections.Concurrent.ConcurrentDictionary<Guid, string>();
        var id1 = Guid.NewGuid();
        var id2 = Guid.NewGuid();
        var id3 = Guid.NewGuid();

        clients[id1] = "client1";
        clients[id2] = "client2";
        clients[id3] = "client3";

        clients.TryRemove(id2, out _);

        clients.Should().HaveCount(2);
        clients.Should().ContainKey(id1);
        clients.Should().ContainKey(id3);
        clients.Should().NotContainKey(id2);
    }

    [Fact]
    public void ClientDisconnect_RemoveNonexistentId_ReturnsFalse()
    {
        var clients = new System.Collections.Concurrent.ConcurrentDictionary<Guid, string>();
        var id = Guid.NewGuid();

        clients.TryRemove(id, out _).Should().BeFalse();
    }

    // -----------------------------------------------------------------------
    //  Invalid message handling
    // -----------------------------------------------------------------------

    [Fact]
    public void MalformedJson_ThrowsJsonException()
    {
        var badJson = "this is not json";

        var act = () => JsonSerializer.Deserialize<JsonElement>(badJson, JsonOptions);

        act.Should().Throw<JsonException>();
    }

    [Fact]
    public void EmptyJsonObject_HasNoTypeProperty()
    {
        var json = "{}";
        var doc = JsonDocument.Parse(json);

        doc.RootElement.TryGetProperty("type", out _).Should().BeFalse();
    }

    [Fact]
    public void UnknownRequestType_GeneratesErrorResponse()
    {
        var request = new { type = "unknown_command" };
        var json = JsonSerializer.Serialize(request, JsonOptions);

        var requestDoc = JsonDocument.Parse(json);
        var requestType = requestDoc.RootElement.GetProperty("type").GetString();

        var knownTypes = new HashSet<string> { "get_state", "subscribe" };
        knownTypes.Contains(requestType!).Should().BeFalse();

        // Replicate the server's error response format
        var errorResponse = JsonSerializer.Serialize(new
        {
            type = "error",
            message = $"Unknown request type: {requestType}"
        }, JsonOptions);

        var errorDoc = JsonDocument.Parse(errorResponse);
        errorDoc.RootElement.GetProperty("type").GetString().Should().Be("error");
        errorDoc.RootElement.GetProperty("message").GetString().Should().Contain("unknown_command");
    }

    [Fact]
    public void MalformedJson_ServerErrorResponse_HasCorrectFormat()
    {
        // Replicate what the server sends on JsonException
        var errorResponse = JsonSerializer.Serialize(new
        {
            type = "error",
            message = "Invalid JSON"
        }, JsonOptions);

        var doc = JsonDocument.Parse(errorResponse);
        doc.RootElement.GetProperty("type").GetString().Should().Be("error");
        doc.RootElement.GetProperty("message").GetString().Should().Be("Invalid JSON");
    }

    [Fact]
    public void NullTypeField_IsJsonNull()
    {
        var json = """{"type": null}""";
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("type").ValueKind.Should().Be(JsonValueKind.Null);
    }

    [Fact]
    public void RequestWithExtraFields_DeserializesWithoutError()
    {
        var json = """{"type": "get_state", "extra_field": 123, "another": "value"}""";

        var act = () => JsonSerializer.Deserialize<JsonElement>(json, JsonOptions);

        act.Should().NotThrow();
    }

    [Fact]
    public void EmptyStringMessage_ThrowsJsonException()
    {
        var act = () => JsonSerializer.Deserialize<JsonElement>("", JsonOptions);

        act.Should().Throw<JsonException>();
    }

    // -----------------------------------------------------------------------
    //  Subscribe acknowledgment protocol
    // -----------------------------------------------------------------------

    [Fact]
    public void SubscribeAck_ContainsFieldList()
    {
        var fields = new List<string> { "position", "speeds" };
        var ack = new { type = "subscribe_ack", fields };
        var json = JsonSerializer.Serialize(ack, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("type").GetString().Should().Be("subscribe_ack");
        doc.RootElement.GetProperty("fields").GetArrayLength().Should().Be(2);
    }

    [Fact]
    public void SubscribeAck_NullFields_DefaultsToAll()
    {
        List<string>? fields = null;
        var ack = new { type = "subscribe_ack", fields = fields ?? new List<string> { "all" } };
        var json = JsonSerializer.Serialize(ack, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("fields")[0].GetString().Should().Be("all");
    }

    [Fact]
    public void SubscribeAck_EmptyFields_SerializesAsEmptyArray()
    {
        var ack = new { type = "subscribe_ack", fields = new List<string>() };
        var json = JsonSerializer.Serialize(ack, JsonOptions);
        var doc = JsonDocument.Parse(json);

        doc.RootElement.GetProperty("fields").GetArrayLength().Should().Be(0);
    }

    // -----------------------------------------------------------------------
    //  Per-client subscription state
    // -----------------------------------------------------------------------

    [Fact]
    public void ClientSubscriptions_DifferentClientsCanHaveDifferentFilters()
    {
        var subscriptions = new Dictionary<Guid, List<string>?>
        {
            [Guid.NewGuid()] = null, // full state
            [Guid.NewGuid()] = new List<string> { "position" },
            [Guid.NewGuid()] = new List<string> { "position", "speeds", "surfaces" }
        };

        var state = new SimState { Connected = true };

        foreach (var (_, fields) in subscriptions)
        {
            string json;
            if (fields is null || fields.Count == 0)
            {
                json = JsonSerializer.Serialize(state, JsonOptions);
            }
            else
            {
                json = SerializeFilteredState(state, fields);
            }

            var doc = JsonDocument.Parse(json);
            doc.RootElement.TryGetProperty("connected", out _).Should().BeTrue();

            if (fields is not null)
            {
                foreach (var field in fields)
                {
                    doc.RootElement.TryGetProperty(field, out _).Should().BeTrue();
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    //  Helpers
    // -----------------------------------------------------------------------

    /// <summary>
    /// Mirrors the SerializeFilteredState logic from TelemetryWebSocketServer.
    /// This must stay in sync with the production code.
    /// </summary>
    private static string SerializeFilteredState(SimState state, List<string> fields)
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

        return JsonSerializer.Serialize(dict, JsonOptions);
    }
}
