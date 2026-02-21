# Avatar Plugin Research - LiveKit Integration

## Übersicht

Dieses Dokument vergleicht die Avaluma-Plugin-Implementierung mit **8 anderen Avatar-Plugins**:

| Plugin | Anbieter | Modus | Besonderheit |
|--------|----------|-------|--------------|
| **Tavus** | Tavus.io | Remote | Persona + Replica System |
| **Simli** | Simli.com | Remote | Face ID + Emotion System |
| **LiveAvatar** | HeyGen | Remote + WebSocket | Komplexeste Implementierung |
| **BitHuman** | BitHuman | **Local + Remote** | Einziger anderer mit Local Mode! |
| **Hedra** | Hedra.com | Remote | Minimalistisch, Image-Upload |
| **Anam** | Anam.ai | Remote | Zwei-Token Auth System |
| **AvatarTalk** | AvatarTalk.ai | Remote | Dual-Participant (Agent + Listener) |
| **Bey** | Bey | Remote | Ähnlich wie Avaluma Remote |

---

## 1. Gesamt-Vergleichsmatrix

| Aspekt | Avaluma | Tavus | Simli | LiveAvatar | BitHuman | Hedra | Anam | AvatarTalk |
|--------|---------|-------|-------|------------|----------|-------|------|------------|
| **Local Mode** | Ja | Nein | Nein | Nein | **Ja** | Nein | Nein | Nein |
| **Remote Mode** | Ja | Ja | Ja | Ja | Ja | Ja | Ja | Ja |
| **Sample Rate** | 16kHz | 24kHz | 16kHz | 24kHz | Variabel | 16kHz | 24kHz | 16kHz |
| **Audio Output** | Queue/DataStream | DataStream | DataStream | Queue+WS | Queue/DataStream | DataStream | DataStream | DataStream |
| **WebSocket** | Nein | Nein | Nein | **Ja** | Nein | Nein | Nein | Nein |
| **Session Cleanup** | Nein | Nein | Nein | **Ja** | Ja | Nein | Nein | **Ja** |
| **Image Upload** | Nein | Nein | Nein | Nein | **Ja** | **Ja** | Nein | Nein |
| **Video Track Wait** | Nein | **Ja** | Nein | Nein | Nein | **Ja** | **Ja** | Nein |

---

## 2. Dein Avaluma Plugin - Analyse

### Dateistruktur
```
src/avaluma_livekit_plugin/
├── __init__.py                    # Plugin-Registrierung, Public API
├── avatar.py                      # AvatarSession (Local + Remote Modi)
├── log.py                         # Logging
├── version.py                     # Version 0.0.3
└── local/
    ├── __init__.py                # C++ Binary Loading
    ├── avatar_cpp_wrapper.py      # AvalumaRuntime (C++ Bridge)
    ├── avatar_runner.py           # AvalumaAvatarRunner
    └── video_generator.py         # AvalumaVideoGenerator
```

### Local Mode - Funktionsweise
1. `AvatarSession(mode="local")` wird erstellt
2. `AvalumaRuntime` lädt C++ Binary (`avaluma_runtime.so`)
3. `AvalumaVideoGenerator` konvertiert C++ Frames zu LiveKit Frames
4. `AvalumaAvatarRunner` streamt Video zum Room mit A/V Sync
5. Audio wird via `QueueAudioOutput` verarbeitet

### Remote Mode - Funktionsweise
1. `AvatarSession(mode="remote")` wird erstellt
2. HTTP POST an HVI Server
3. Remote Server startet Avatar als separaten Agent
4. Audio wird via `DataStreamAudioOutput` gesendet

---

## 3. Detailanalyse: Alle 8 Plugins

### 3.1 Tavus

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://tavusapi.com/v2` |
| Auth | `x-api-key` Header |
| Sample Rate | 24000 Hz |
| Audio Output | DataStreamAudioOutput |

**Session Flow:**
```python
# 1. Conversation erstellen
api.create_conversation(replica_id, persona_id, ...)

# 2. Token mit ATTRIBUTE_PUBLISH_ON_BEHALF
token = api.AccessToken(...).with_attributes(...)

# 3. Audio Output setzen
agent_session.output.audio = DataStreamAudioOutput(...)
```

**Besonderheiten:**
- Wartet auf Remote Video Track bevor Audio gestreamt wird
- Pipeline Mode: "echo"
- Persona kann on-the-fly erstellt werden

---

### 3.2 Simli

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://api.simli.ai` |
| Auth | API Key in Config |
| Sample Rate | 16000 Hz |
| Audio Output | DataStreamAudioOutput |

**Session Flow (2-Step):**
```python
# 1. Audio-to-Video Session starten
session_token = api.startAudioToVideoSession(face_id, ...)

# 2. LiveKit Agents Session starten
api.StartLivekitAgentsSession(session_token, livekit_url, ...)
```

**Konfiguration:**
```python
@dataclass
class SimliConfig:
    api_key: str
    face_id: str
    emotion_id: str = "92f24a0c-..."  # happy_0
    max_session_length: int = 600
    max_idle_time: int = 30
```

**Besonderheiten:**
- Automatisches Silence Handling
- Session Timeout + Idle Detection
- Sync Audio mit Video (`syncAudio: True`)

---

### 3.3 LiveAvatar (HeyGen) - Komplexeste Implementierung

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://api.liveavatar.com/v1/sessions` |
| Auth | `X-API-KEY` Header |
| Sample Rate | 24000 Hz |
| Audio Output | QueueAudioOutput + WebSocket |

**Session Flow (3-Step + WebSocket):**
```python
# 1. Streaming Session erstellen
session_id, session_token = api.create_streaming_session(avatar_id, ...)

# 2. Streaming starten -> WebSocket URL
ws_url = api.start_streaming_session(session_id, session_token)

# 3. WebSocket verbinden
ws = await websockets.connect(ws_url)
```

**WebSocket Events:**
```python
# Agent -> Server
"agent.speak"          # Audio senden
"agent.interrupt"      # Unterbrechung
"agent.speak_end"      # Sprache beendet
"agent.start_listening"
"agent.stop_listening"
```

**Einzigartige Features:**
- **Audio Resampling:** Dynamischer AudioResampler
- **State Management:** Trackt `audio_playing`, `playback_position`
- **Event Integration:** Reagiert auf Agent State Changes
- **Graceful Shutdown:** `stop_streaming_session()` API

---

### 3.4 BitHuman - Einziger anderer mit Local Mode!

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://auth.api.bithuman.ai/v1/runtime-tokens/request` |
| Auth | `api_secret` oder `api_token` |
| Sample Rate | Variabel (Runtime-abhängig) |
| Audio Output | QueueAudioOutput (local) / DataStreamAudioOutput (cloud) |

**Automatische Modus-Erkennung:**
```python
self._mode = (
    "cloud" if utils.is_given(avatar_image) or utils.is_given(avatar_id) 
    else "local"
)
```

**Local Mode:**
```python
# Benötigt:
# - BITHUMAN_MODEL_PATH (lokales SDK)
# - BITHUMAN_API_SECRET oder BITHUMAN_API_TOKEN

runtime = await AsyncBithuman.create(model_path=..., model="essence")
generator = BithumanGenerator(runtime)
```

**Cloud Mode:**
```python
# HTTP POST mit avatar_image (PIL Image, URL, oder Bytes)
# Unterstützt GPU/CPU Modus basierend auf Model-Typ
```

**Model Types:**
| Model | Beschreibung |
|-------|--------------|
| `expression` | Dynamische Emotionen, generativ |
| `essence` | Vordefinierte Aktionen, konsistent |

**Einzigartige Features:**
- **Image Upload:** PIL Image, File Path, oder URL
- **Runtime Reusability:** `runtime._regenerate_transaction_id()`
- **Shutdown Callback:** Registriert cleanup mit job context

---

### 3.5 Hedra - Minimalistisch

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://api.hedra.com/public/livekit/v1/session` |
| Auth | `x-api-key` Header |
| Sample Rate | 16000 Hz |
| Audio Output | DataStreamAudioOutput |

**Session Flow (1-Step mit FormData):**
```python
# Multipart Form Data (nicht JSON!)
data = aiohttp.FormData({
    "livekit_url": livekit_url, 
    "livekit_token": livekit_token
})

# Optional: Avatar Image Upload
if avatar_image:
    data.add_field("avatar_image", img_bytes, 
                   filename="avatar.jpg", 
                   content_type="image/jpeg")
```

**Besonderheiten:**
- Stateless Design - kein Session State Management
- Image-basierte Avatars (JPEG, quality=95)
- Wartet auf Video Track: `wait_remote_track=rtc.TrackKind.KIND_VIDEO`

---

### 3.6 Anam

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://api.anam.ai` |
| Auth | Bearer Token |
| Sample Rate | 24000 Hz |
| Audio Output | DataStreamAudioOutput |

**Zwei-Token Authentifizierung:**
```python
# Step 1: API Key -> Session Token
session_token = await api.create_session_token(persona_config, livekit_url, livekit_token)

# Step 2: Session Token -> Engine Session
session = await api.start_engine_session(session_token)
```

**PersonaConfig:**
```python
@dataclass
class PersonaConfig:
    name: str           # Avatar Display Name
    avatarId: str       # Anam Avatar ID
```

**Besonderheiten:**
- Video Track Waiting: `wait_remote_track=rtc.TrackKind.KIND_VIDEO`
- Publish-On-Behalf Attribute für Avatar
- Minimiert Long-lived Credential Exposure

---

### 3.7 AvatarTalk

| Eigenschaft | Wert |
|-------------|------|
| API URL | `https://api.avatartalk.ai` |
| Auth | Bearer Token |
| Sample Rate | 16000 Hz |
| Audio Output | DataStreamAudioOutput |

**Dual-Participant Model:**
```python
# Zwei Tokens werden generiert:
livekit_token = generate_token(identity="avatartalk-agent", kind="agent")
livekit_listener_token = generate_token(identity="listener")

# Beide werden an API gesendet
await api.start_session(
    livekit_token=livekit_token,
    livekit_listener_token=livekit_listener_token,
    avatar="japanese_man",
    emotion="expressive"
)
```

**Konfiguration:**
```python
# Defaults
DEFAULT_AVATAR_NAME = "japanese_man"
DEFAULT_AVATAR_EMOTION = "expressive"
```

**Einzigartige Features:**
- **Session Cleanup:** `stop_session(task_id)` bei Shutdown
- **Task ID Tracking:** Maps room names zu task IDs
- **Dual-Token:** Separate Tokens für Agent und Listener

---

### 3.8 Bey

| Eigenschaft | Wert |
|-------------|------|
| Modus | Remote only |
| Architektur | Ähnlich wie Avaluma Remote |

**Analyse:** Bey folgt dem Standard-Pattern wie die anderen Remote-Plugins mit HTTP API und DataStreamAudioOutput.

---

## 4. Feature-Vergleich: Was andere haben

### 4.1 Local Mode (nur Avaluma + BitHuman)

| Feature | Avaluma | BitHuman |
|---------|---------|----------|
| C++ Runtime | Ja (`avaluma_runtime.so`) | Ja (`AsyncBithuman`) |
| Model Types | Single | `expression` / `essence` |
| GPU Support | Ja (EGL) | Ja (GPU/CPU Mode) |
| Offline-fähig | Ja | Ja |

**Dein Vorteil:** Du hast bereits Local Mode - das haben nur 2 von 9 Plugins!

---

### 4.2 Session Cleanup / Stop Endpoint

| Plugin | Hat Stop Endpoint | Implementierung |
|--------|-------------------|-----------------|
| Avaluma | **Nein** | - |
| LiveAvatar | Ja | `stop_streaming_session(session_id, session_token)` |
| AvatarTalk | Ja | `stop_session(task_id)` |
| BitHuman | Ja | `runtime.cleanup()` via shutdown callback |
| Andere | Nein | - |

**Empfehlung:** Stop-Endpoint für deinen Remote Server hinzufügen.

---

### 4.3 Video Track Waiting

| Plugin | Wartet auf Video | Code |
|--------|------------------|------|
| Avaluma | **Nein** | - |
| Tavus | Ja | `wait_remote_track=rtc.TrackKind.KIND_VIDEO` |
| Hedra | Ja | `wait_remote_track=rtc.TrackKind.KIND_VIDEO` |
| Anam | Ja | `wait_remote_track=rtc.TrackKind.KIND_VIDEO` |
| Andere | Nein | - |

**Empfehlung:** Für Remote Mode hinzufügen um Race Conditions zu vermeiden.

---

### 4.4 WebSocket Kommunikation (nur LiveAvatar)

LiveAvatar ist das einzige Plugin mit WebSocket:
```python
# Vorteile:
- Echtzeit Events (speak, interrupt, etc.)
- Präzise Playback Position Tracking
- Sofortige Unterbrechungen
- Bidirektionale Kommunikation
```

**Frage an dich:** Benötigst du WebSocket für deinen Remote Server?

---

### 4.5 Image Upload (BitHuman + Hedra)

```python
# BitHuman: Flexibel
avatar_image: Image.Image | str  # PIL Image, File Path, oder URL

# Hedra: JPEG Upload
data.add_field("avatar_image", img_bytes, filename="avatar.jpg")
```

**Frage an dich:** Willst du Custom Avatar Images unterstützen?

---

### 4.6 Audio Sample Rate Vergleich

| Sample Rate | Plugins |
|-------------|---------|
| 16000 Hz | Avaluma, Simli, Hedra, AvatarTalk |
| 24000 Hz | Tavus, LiveAvatar, Anam |
| Variabel | BitHuman |

**Empfehlung:** 16kHz ist OK für Sprache. 24kHz nur wenn bessere Qualität benötigt.

---

## 5. Architektur-Patterns der anderen Plugins

### 5.1 Standard Remote Pattern (Tavus, Simli, Hedra, Anam, AvatarTalk, Bey)

```
┌─────────────────┐     HTTP POST      ┌─────────────────┐
│  Local Agent    │ ─────────────────> │  Cloud Service  │
│                 │                    │                 │
│  - Generates    │     LiveKit        │  - Renders      │
│    Audio        │ <────────────────> │    Avatar Video │
│  - DataStream   │     Room           │  - Joins as     │
│    AudioOutput  │                    │    Participant  │
└─────────────────┘                    └─────────────────┘
```

### 5.2 WebSocket Pattern (LiveAvatar)

```
┌─────────────────┐     HTTP POST      ┌─────────────────┐
│  Local Agent    │ ─────────────────> │  Cloud Service  │
│                 │                    │                 │
│  - Audio via    │     WebSocket      │  - Renders      │
│    Queue        │ <────────────────> │    Avatar       │
│  - Events       │   (bidirektional)  │  - State Sync   │
│  - State Track  │                    │  - Events       │
└─────────────────┘                    └─────────────────┘
```

### 5.3 Local + Remote Hybrid Pattern (Avaluma, BitHuman)

```
┌─────────────────────────────────────────────────────────┐
│                      AvatarSession                       │
│                                                          │
│   mode="local"              │        mode="remote"       │
│   ─────────────             │        ─────────────       │
│   ┌───────────┐             │        ┌───────────┐       │
│   │ C++ Runtime│            │        │ HTTP POST │       │
│   │ (GPU/CPU)  │            │        │ to Server │       │
│   └─────┬─────┘             │        └─────┬─────┘       │
│         │                   │              │             │
│   ┌─────▼─────┐             │        ┌─────▼─────┐       │
│   │VideoGen   │             │        │DataStream │       │
│   │+ Runner   │             │        │AudioOutput│       │
│   └───────────┘             │        └───────────┘       │
└─────────────────────────────────────────────────────────┘
```

---

## 6. Konkrete Empfehlungen für Avaluma

### 6.1 Sofort umsetzen (Wichtig)

1. **Session Cleanup für Remote Mode**
   ```python
   async def stop(self):
       if self._mode == "remote" and self._session_id:
           await self._request_remote_avatar_to_leave()
   ```

2. **Video Track Waiting (Remote Mode)**
   ```python
   agent_session.output.audio = DataStreamAudioOutput(
       room=room,
       destination_identity=avatar_identity,
       wait_remote_track=rtc.TrackKind.KIND_VIDEO,  # Hinzufügen!
       sample_rate=SAMPLE_RATE,
   )
   ```

3. **Session ID vom Server zurückgeben**
   ```python
   # Server Response:
   {
       "session_id": "abc123",
       "avatar_participant_identity": "avaluma-avatar-abc123"
   }
   
   # Client speichert:
   self._session_id = response["session_id"]
   ```

### 6.2 Mittelfristig (Nice to have)

1. **Shutdown Callback registrieren** (wie BitHuman/AvatarTalk)
   ```python
   job_ctx = get_job_context()
   job_ctx.add_shutdown_callback(self._on_shutdown)
   
   async def _on_shutdown(self):
       await self.stop()
   ```

2. **Config Dataclass** (wie Simli)
   ```python
   @dataclass
   class AvalumaConfig:
       license_key: str
       avatar_id: str
       mode: Literal["local", "remote"] = "local"
       assets_dir: Optional[str] = None
       hvi_server_url: Optional[str] = None
       sample_rate: int = 16000
   ```

3. **Retry-Logik mit Backoff**
   ```python
   for i in range(self._conn_options.max_retry):
       try:
           response = await self._http_post(...)
           break
       except Exception:
           if i < self._conn_options.max_retry - 1:
               await asyncio.sleep(self._conn_options.retry_interval)
           else:
               raise APIConnectionError("Failed after all retries")
   ```

### 6.3 Langfristig (Optional)

1. **WebSocket für Remote Mode** (wie LiveAvatar)
   - Echtzeit-Events
   - Interrupt Support
   - Playback Position Tracking

2. **Image Upload Support** (wie BitHuman/Hedra)
   - Custom Avatar Images
   - PIL Image oder URL

3. **Audio Resampling** (wie LiveAvatar)
   ```python
   self._resampler = AudioResampler(
       input_rate=agent_session.output.sample_rate,
       output_rate=24000
   )
   ```

---

## 7. Dein Alleinstellungsmerkmal

### Local + Remote Mode - Fast einzigartig!

| Plugin | Local Mode | Remote Mode | Beides |
|--------|------------|-------------|--------|
| **Avaluma** | Ja | Ja | **Ja** |
| **BitHuman** | Ja | Ja | **Ja** |
| Tavus | Nein | Ja | Nein |
| Simli | Nein | Ja | Nein |
| LiveAvatar | Nein | Ja | Nein |
| Hedra | Nein | Ja | Nein |
| Anam | Nein | Ja | Nein |
| AvatarTalk | Nein | Ja | Nein |

**Nur du und BitHuman bieten echten Local Mode!**

**Vorteile deines Ansatzes:**
- Kunden können wählen: Latenz vs. Infrastruktur-Kosten
- Offline-Nutzung möglich
- Edge-Deployment möglich
- Keine Cloud-Abhängigkeit für Local Mode

---

## 8. API-Vergleich für Remote Server

### Dein aktueller API Call:
```python
POST {hvi_server_url}
Headers: {"api-secret": license_key}
Body: {
    "livekit_url": str,
    "livekit_token": str,
    "livekit_room_name": str,
    "avaluma_license_key": str,
    "avaluma_avatar_id": str
}
```

### Empfohlene Server API:

```python
# 1. Session erstellen
POST /sessions
Headers: {"api-secret": license_key}
Body: {
    "livekit_url": str,
    "livekit_token": str,
    "livekit_room_name": str,
    "avatar_id": str
}
Response: {
    "session_id": "abc123",
    "avatar_participant_identity": "avaluma-avatar-abc123",
    "status": "starting"
}

# 2. Session beenden
DELETE /sessions/{session_id}
Headers: {"api-secret": license_key}
Response: {"status": "stopped"}

# 3. Optional: Status abfragen
GET /sessions/{session_id}
Headers: {"api-secret": license_key}
Response: {
    "session_id": "abc123",
    "status": "active" | "stopped" | "error",
    "avatar_participant_identity": "avaluma-avatar-abc123"
}
```

---

## 9. Zusammenfassung

### Was du gut machst:
- **Dual-Mode Architektur** (Local + Remote) - fast einzigartig!
- Saubere Trennung (Runtime, VideoGenerator, Runner)
- A/V Synchronisation mit Timestamps im Local Mode
- Einfache Session-Erstellung

### Was du verbessern solltest:
1. **Session Cleanup** für Remote Mode (Stop-Endpoint)
2. **Video Track Waiting** vor Audio-Streaming (Remote)
3. **Shutdown Callback** registrieren

### Optional für später:
- WebSocket für Echtzeit-Events
- Image Upload Support
- Audio Resampling
- Config Dataclass

### Offene Fragen an dich:
1. Hat dein HVI-Server bereits einen Stop-Endpoint?
2. Brauchst du WebSocket-Kommunikation oder reicht HTTP?
3. Willst du Custom Avatar Images unterstützen?
4. Ist 16kHz Sample Rate ausreichend?

---

*Erstellt am: 2024-12-10*
*Basierend auf Analyse von: Tavus, Simli, LiveAvatar, BitHuman, Hedra, Anam, AvatarTalk, Bey*
