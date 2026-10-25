# Polar upload protocol

This is how `workout_sync/polar.py` uploads a session. The endpoints belong to the retired
Polar Beat phone app. They are undocumented and can change at any time.

Base URL: `https://www.polarremote.com/v2`

Every request sends the Polar Beat app's client credentials as HTTP Basic auth
(`Polar Beat2 Android:Beat2AndroidApiKey`) and keeps the session cookies from the login.

## Calls

| # | Request | Body | Answer |
| --- | --- | --- | --- |
| 1 | `POST /login/user/associateToApp` | JSON `{"username", "password"}` | `200`, JSON with the user `id` |
| 2 | `POST /users/{id}/training-sessions/create` | `PbTrainingSession` | `201` + `Location` of the new session. `200` = a session with that start time already exists. |
| 3 | `POST {session}/exercises/create` | `PbExerciseBase` | `201` + `Location` of the exercise |
| 4 | `POST {exercise}/statistics` | `PbExerciseStatistics` | `2xx` |
| 5 | `POST {exercise}/samples` | `PbExerciseSamples` | `2xx` |
| 6 | `POST {exercise}/route` | `PbExerciseRouteSamples` | `2xx` |
| 7 | `POST {exercise}/finalize?success=true` (falls back to `{session}/finalize`) | empty form | `2xx`. The result is not checked. |

Protobuf bodies use `Content-Type: application/x-protobuf`.

## Messages (proto2)

Only the fields that are sent are listed.

```proto
message PbDate          { uint32 year = 1; uint32 month = 2; uint32 day = 3; }
message PbTime          { uint32 hour = 1; uint32 minute = 2; uint32 seconds = 3; uint32 millis = 4; }
message PbLocalDateTime { PbDate date = 1; PbTime time = 2; bool trusted = 3;
                          int32 time_zone_offset = 4; /* minutes */ }
message PbDuration      { uint32 hours = 1; uint32 minutes = 2; uint32 seconds = 3; uint32 millis = 4; }

message PbTrainingSession {
  PbLocalDateTime start = 1;  uint32 exercise_count = 2;  string model_name = 4;
  PbDuration duration = 5;    float distance = 6;  /* m */  uint32 calories = 7;
  message Note { string text = 1; }  Note note = 13;
}

message PbExerciseBase {
  PbLocalDateTime start = 1;  PbDuration duration = 2;
  message Sport { uint64 value = 1; }  Sport sport = 3;   // Polar sport id
  float distance = 4;  uint32 calories = 5;  float ascent = 10;  float descent = 11;
  double latitude = 12;  double longitude = 13;            // start position
}

message PbExerciseStatistics {
  message HeartRate { uint32 minimum = 1; uint32 average = 2; uint32 maximum = 3; }
  message Speed     { float average = 1; float maximum = 2; }            // km/h
  message Altitude  { float minimum = 1; float average = 2; float maximum = 3; }
  HeartRate heart_rate = 1;  Speed speed = 2;  Altitude altitude = 4;
}

message PbExerciseSamples {               // one value per recording_interval (1 s)
  PbDuration recording_interval = 1;
  repeated uint32 heart_rate = 2 [packed = true];   // bpm, 0 = no reading
  repeated float altitude    = 6 [packed = true];   // m
  repeated float speed       = 9 [packed = true];   // km/h
  repeated float distance    = 11 [packed = true];  // cumulative m
}

message PbExerciseRouteSamples {
  repeated uint32 duration    = 1 [packed = true];  // ms since start
  repeated double latitude    = 2 [packed = true];
  repeated double longitude   = 3 [packed = true];
  repeated sint32 gps_altitude = 4 [packed = true]; // m
}
```

## Sport ids

Polar does not publish its sport ids. `workout_sync/sports.py` uses the community list at
<https://github.com/pcolby/bipolar/wiki/Polar-Sport-Types>.

## Not supported

Laps, cadence and power are not sent to Polar. Their field numbers are not known.
