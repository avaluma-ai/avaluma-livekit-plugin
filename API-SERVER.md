# Avaluma HVI Server - API Spezifikation

Dieses Dokument beschreibt die Anforderungen an den Avaluma HVI (Remote Avatar) Server, basierend auf der Plugin-Implementierung und Best Practices aus anderen Avatar-Plugins.

---

## 1. Übersicht

Der HVI Server ist verantwortlich für:
- Empfangen von Avatar-Start-Requests vom Plugin
- Starten eines Avatar-Agents der dem LiveKit Room beitritt
- Rendern des Avatars und Streamen von Video zum Room
- Empfangen von Audio via LiveKit DataStream
- Automatisches Cleanup wenn der Room leer wird

```
┌─────────────────┐                      ┌─────────────────┐
│  Client Agent   │                      │   HVI Server    │
│  (Plugin)       │                      │                 │
│                 │  POST /start-avatar  │                 │
│                 │ ───────────────────> │  Startet Avatar │
│                 │  {session_id: ...}   │  Agent Process  │
│                 │ <─────────────────── │                 │
│                 │                      │        │        │
│                 │                      │        ▼        │
│                 │     LiveKit Room     │  ┌───────────┐  │
│                 │ <══════════════════> │  │  Avatar   │  │
│   Audio ───────>│                      │  │  Agent    │  │
│   <───── Video  │                      │  └───────────┘  │
└─────────────────┘                      └─────────────────┘
```

---

## 2. API Endpoints

### 2.1 Start Avatar Session

**Endpoint:** `POST /v1/livekit/start-avatar`

**Headers:**
```
Content-Type: application/json
api-secret: <avaluma_license_key>
```

**Request Body:**
```json
{
    "livekit_url": "wss://your-livekit-server.com",
    "livekit_token": "<jwt_token>",
    "livekit_room_name": "room-123",
    "avaluma_license_key": "<license_key>",
    "avaluma_avatar_id": "avatar-001"
}
```

**Response (Success - 200):**
```json
{
    "session_id": "sess_abc123def456",
    "status": "started",
    "avatar_participant_identity": "avatar-avatar-001"
}
```

**Response (Error - 4xx/5xx):**
```json
{
    "error": "Invalid license key",
    "code": "INVALID_LICENSE"
}
```

#### Request Parameter Details:

| Parameter | Typ | Beschreibung |
|-----------|-----|--------------|
| `livekit_url` | string | WebSocket URL zum LiveKit Server |
| `livekit_token` | string | JWT Token mit Room-Zugriff und `lk.publish_on_behalf` Attribut |
| `livekit_room_name` | string | Name des LiveKit Rooms |
| `avaluma_license_key` | string | Avaluma Lizenzschlüssel zur Validierung |
| `avaluma_avatar_id` | string | ID des zu verwendenden Avatars |

#### JWT Token Inhalt (vom Plugin generiert):

```python
{
    "sub": "avatar-{avatar_id}",           # Identity
    "name": "Avatar-{avatar_id}",          # Display Name
    "video": {
        "room_join": True,
        "room": "{room_name}"
    },
    "attributes": {
        "lk.publish_on_behalf": "{local_agent_identity}"
    },
    "kind": "agent"
}
```

---

### 2.2 Stop Avatar Session (Optional)

**Endpoint:** `POST /v1/livekit/stop-avatar`

**Headers:**
```
Content-Type: application/json
api-secret: <avaluma_license_key>
```

**Request Body:**
```json
{
    "session_id": "sess_abc123def456"
}
```

**Response (Success - 200):**
```json
{
    "status": "stopped"
}
```

> **Hinweis:** Dieser Endpoint ist optional, da der Avatar auch automatisch stoppen sollte wenn der Room leer wird (siehe Abschnitt 4).

---

## 3. Avatar Agent Verhalten

### 3.1 Room Beitritt

Der Avatar Agent muss:

1. **Mit dem bereitgestellten Token dem Room beitreten**
   ```python
   room = rtc.Room()
   await room.connect(livekit_url, livekit_token)
   ```

2. **Video Track publishen**
   - Format: RGB oder YUV
   - Auflösung: Abhängig vom Avatar (z.B. 512x640)
   - FPS: 25 (empfohlen)

3. **Audio via DataStream empfangen**
   - Der Client sendet Audio über LiveKit DataStream
   - Sample Rate: 16000 Hz
   - Format: PCM int16 mono

### 3.2 Audio Empfang

Der Avatar Agent empfängt Audio über den LiveKit DataStream Mechanismus:

```python
# Beispiel: Audio empfangen
@room.on("data_received")
async def on_data(data: bytes, participant: rtc.Participant):
    # Audio-Daten verarbeiten und an Avatar-Runtime weiterleiten
    await avatar_runtime.push_audio(data)
```

### 3.3 Video Streaming

Der Avatar rendert Frames und published sie zum Room:

```python
# Beispiel: Video publishen
video_source = rtc.VideoSource(width=512, height=640)
track = rtc.LocalVideoTrack.create_video_track("avatar-video", video_source)
await room.local_participant.publish_track(track)

# Frame senden
frame = avatar_runtime.render_frame()
video_source.capture_frame(frame)
```

---

## 4. Automatisches Session Cleanup

### 4.1 Room-Leer-Erkennung (Empfohlen)

Der Avatar sollte automatisch stoppen wenn keine anderen Teilnehmer mehr im Room sind:

```python
@room.on("participant_disconnected")
async def on_participant_left(participant: rtc.Participant):
    # Prüfen ob noch andere Teilnehmer da sind (außer Avatar selbst)
    other_participants = [
        p for p in room.remote_participants.values()
    ]
    
    if len(other_participants) == 0:
        logger.info("Room is empty, stopping avatar")
        await cleanup_and_exit()
```

### 4.2 Timeout (Optional)

Zusätzlich kann ein Idle-Timeout implementiert werden:

```python
IDLE_TIMEOUT = 30  # Sekunden ohne Audio

last_audio_time = time.time()

async def check_idle():
    while running:
        if time.time() - last_audio_time > IDLE_TIMEOUT:
            logger.info("Idle timeout reached, stopping avatar")
            await cleanup_and_exit()
        await asyncio.sleep(5)
```

### 4.3 Graceful Shutdown

Beim Beenden sollte der Avatar:

1. Video Track unpublishen
2. Room verlassen
3. Ressourcen freigeben (GPU, Memory)
4. Session aus internem State entfernen

```python
async def cleanup_and_exit():
    # 1. Tracks unpublishen
    for track in room.local_participant.tracks.values():
        await room.local_participant.unpublish_track(track.sid)
    
    # 2. Room verlassen
    await room.disconnect()
    
    # 3. Avatar Runtime cleanup
    avatar_runtime.cleanup()
    
    # 4. Session entfernen
    del active_sessions[session_id]
```

---

## 5. Session Management

### 5.1 Session ID Generierung

```python
import uuid

def generate_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"
```

### 5.2 Aktive Sessions Tracking

```python
active_sessions: dict[str, AvatarSession] = {}

class AvatarSession:
    session_id: str
    room_name: str
    avatar_id: str
    created_at: datetime
    status: Literal["starting", "active", "stopping", "stopped"]
```

### 5.3 Session Lookup für Stop-Endpoint

```python
@app.post("/v1/livekit/stop-avatar")
async def stop_avatar(request: StopRequest):
    session = active_sessions.get(request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    await session.stop()
    return {"status": "stopped"}
```

---

## 6. Authentifizierung & Sicherheit

### 6.1 License Key Validierung

Jeder Request muss den `api-secret` Header enthalten:

```python
@app.middleware("http")
async def validate_license(request: Request, call_next):
    api_secret = request.headers.get("api-secret")
    
    if not api_secret:
        return JSONResponse(401, {"error": "Missing api-secret header"})
    
    if not await validate_license_key(api_secret):
        return JSONResponse(403, {"error": "Invalid license key"})
    
    return await call_next(request)
```

### 6.2 Rate Limiting (Empfohlen)

```python
# Pro License Key:
MAX_CONCURRENT_SESSIONS = 10
MAX_REQUESTS_PER_MINUTE = 60
```

### 6.3 JWT Token Validierung (Optional)

Der Server kann optional den LiveKit Token validieren:

```python
from livekit import api

def validate_token(token: str, api_key: str, api_secret: str) -> bool:
    try:
        claims = api.AccessToken.verify(token, api_key, api_secret)
        return claims is not None
    except Exception:
        return False
```

---

## 7. Error Handling

### 7.1 HTTP Status Codes

| Code | Bedeutung |
|------|-----------|
| 200 | Erfolg |
| 400 | Ungültiger Request (fehlende Parameter) |
| 401 | Fehlender api-secret Header |
| 403 | Ungültiger License Key |
| 404 | Session nicht gefunden (bei stop) |
| 409 | Avatar bereits in diesem Room aktiv |
| 429 | Rate Limit überschritten |
| 500 | Interner Server-Fehler |
| 503 | Server überlastet |

### 7.2 Error Response Format

```json
{
    "error": "Human-readable error message",
    "code": "ERROR_CODE",
    "details": {
        "field": "additional info"
    }
}
```

### 7.3 Error Codes

| Code | Beschreibung |
|------|--------------|
| `INVALID_LICENSE` | License Key ungültig oder abgelaufen |
| `MISSING_PARAMETER` | Pflichtfeld fehlt |
| `AVATAR_NOT_FOUND` | Avatar ID existiert nicht |
| `SESSION_NOT_FOUND` | Session ID existiert nicht |
| `ROOM_JOIN_FAILED` | Konnte Room nicht beitreten |
| `AVATAR_ALREADY_ACTIVE` | Avatar bereits in diesem Room |
| `RATE_LIMIT_EXCEEDED` | Zu viele Requests |
| `INTERNAL_ERROR` | Interner Fehler |

---

## 8. Logging & Monitoring

### 8.1 Empfohlene Log Events

```python
# Session Start
logger.info("Avatar session started", extra={
    "session_id": session_id,
    "room_name": room_name,
    "avatar_id": avatar_id,
})

# Session End
logger.info("Avatar session ended", extra={
    "session_id": session_id,
    "reason": "room_empty" | "stop_request" | "error",
    "duration_seconds": duration,
})

# Errors
logger.error("Avatar session failed", extra={
    "session_id": session_id,
    "error": str(e),
})
```

### 8.2 Metriken (Optional)

- `avatar_sessions_active` - Gauge: Aktive Sessions
- `avatar_session_duration_seconds` - Histogram: Session-Dauer
- `avatar_api_requests_total` - Counter: API Requests
- `avatar_api_errors_total` - Counter: API Fehler

---

## 9. Deployment Hinweise

### 9.1 Ressourcen pro Avatar Session

| Ressource | Empfehlung |
|-----------|------------|
| CPU | 1-2 Cores |
| RAM | 2-4 GB |
| GPU | Optional (für schnelleres Rendering) |

### 9.2 Skalierung

```
                    ┌─────────────────┐
                    │  Load Balancer  │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  HVI Server 1 │    │  HVI Server 2 │    │  HVI Server 3 │
│  (N Sessions) │    │  (N Sessions) │    │  (N Sessions) │
└───────────────┘    └───────────────┘    └───────────────┘
```

### 9.3 Health Check Endpoint

```python
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "active_sessions": len(active_sessions),
        "version": "1.0.0"
    }
```

---

## 10. Beispiel-Implementierung (Python/FastAPI)

```python
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import uuid

app = FastAPI()

# Models
class StartAvatarRequest(BaseModel):
    livekit_url: str
    livekit_token: str
    livekit_room_name: str
    avaluma_license_key: str
    avaluma_avatar_id: str

class StartAvatarResponse(BaseModel):
    session_id: str
    status: str
    avatar_participant_identity: str

class StopAvatarRequest(BaseModel):
    session_id: str

# State
active_sessions: dict[str, dict] = {}

# Endpoints
@app.post("/v1/livekit/start-avatar", response_model=StartAvatarResponse)
async def start_avatar(
    request: StartAvatarRequest,
    api_secret: str = Header(..., alias="api-secret")
):
    # 1. Validate license
    if not await validate_license(api_secret):
        raise HTTPException(403, {"error": "Invalid license", "code": "INVALID_LICENSE"})
    
    # 2. Generate session ID
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    
    # 3. Start avatar agent (in background)
    avatar_task = asyncio.create_task(
        run_avatar_agent(
            session_id=session_id,
            livekit_url=request.livekit_url,
            livekit_token=request.livekit_token,
            avatar_id=request.avaluma_avatar_id,
        )
    )
    
    # 4. Track session
    active_sessions[session_id] = {
        "task": avatar_task,
        "room_name": request.livekit_room_name,
        "avatar_id": request.avaluma_avatar_id,
    }
    
    # 5. Return response
    return StartAvatarResponse(
        session_id=session_id,
        status="started",
        avatar_participant_identity=f"avatar-{request.avaluma_avatar_id}"
    )

@app.post("/v1/livekit/stop-avatar")
async def stop_avatar(
    request: StopAvatarRequest,
    api_secret: str = Header(..., alias="api-secret")
):
    session = active_sessions.get(request.session_id)
    if not session:
        raise HTTPException(404, {"error": "Session not found", "code": "SESSION_NOT_FOUND"})
    
    # Cancel the avatar task
    session["task"].cancel()
    del active_sessions[request.session_id]
    
    return {"status": "stopped"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "active_sessions": len(active_sessions)
    }
```

---

## 11. Checkliste für Implementierung

### Must Have:
- [ ] `POST /v1/livekit/start-avatar` Endpoint
- [ ] License Key Validierung (`api-secret` Header)
- [ ] Session ID in Response zurückgeben
- [ ] Avatar joint Room mit bereitgestelltem Token
- [ ] Video Track publishen
- [ ] Audio via DataStream empfangen
- [ ] Automatisches Cleanup wenn Room leer wird

### Should Have:
- [ ] `POST /v1/livekit/stop-avatar` Endpoint
- [ ] `GET /health` Endpoint
- [ ] Strukturierte Error Responses
- [ ] Logging mit Session IDs

### Nice to Have:
- [ ] Rate Limiting
- [ ] Metriken/Monitoring
- [ ] Idle Timeout
- [ ] Session Status Endpoint

---

*Erstellt basierend auf Plugin-Code und RESEARCH.md*
*Stand: 2024-12-10*
